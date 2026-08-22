"""Read-only branch status dashboard."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from .git_utils import find_git_root

_STATUS_ORDER = {"READY": 0, "CONFLICT": 1, "MERGED": 2, "ABSORBED": 3}
_STATUS_STYLE = {
    "READY": "bold green",
    "CONFLICT": "bold red",
    "MERGED": "green",
    "ABSORBED": "cyan",
}


@dataclass(frozen=True, slots=True)
class BranchStatus:
    name: str
    ahead: int
    behind: int
    last_commit: int
    status: str
    merge: str
    stale: bool
    worktree: Path | None
    dirty: bool


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result


def _branches(root: Path) -> list[tuple[str, int]]:
    result = _git(
        root,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:unix)",
        "refs/heads/",
    )
    branches: list[tuple[str, int]] = []
    for line in result.stdout.splitlines():
        if line:
            name, timestamp = line.split("\t", 1)
            branches.append((name, int(timestamp)))
    return branches


def _worktrees(root: Path) -> dict[str, Path]:
    result = _git(root, "worktree", "list", "--porcelain", "-z")
    worktrees: dict[str, Path] = {}
    path: Path | None = None
    for field in result.stdout.split("\0"):
        if field.startswith("worktree "):
            path = Path(field.removeprefix("worktree "))
        elif field.startswith("branch refs/heads/") and path is not None:
            worktrees[field.removeprefix("branch refs/heads/")] = path
    return worktrees


def _ahead_behind(root: Path, base: str, branch: str) -> tuple[int, int]:
    result = _git(root, "rev-list", "--left-right", "--count", f"{base}...{branch}")
    parts = result.stdout.split()
    if len(parts) != 2:
        raise RuntimeError(f"unexpected git rev-list output: {result.stdout!r}")
    return int(parts[1]), int(parts[0])


def _absorbed(root: Path, base: str, branch: str) -> bool:
    if _git(root, "rev-list", "--merges", f"{base}..{branch}").stdout:
        return False
    lines = _git(root, "cherry", base, branch).stdout.splitlines()
    if not lines:
        return False
    for line in lines:
        if line.startswith("+ "):
            return False
        if not line.startswith("- "):
            raise RuntimeError(f"unexpected git cherry output: {line!r}")
    return True


def _mergeability(root: Path, base: str, branch: str, behind: int) -> str:
    if behind == 0:
        return "ff"
    result = _git(root, "merge-tree", "--write-tree", base, branch, check=False)
    if result.returncode == 0:
        return "clean"
    if result.returncode == 1:
        return "conflict"
    detail = result.stderr.strip() or result.stdout.strip()
    raise RuntimeError(detail or f"git merge-tree failed for {branch}")


def collect_branch_status(
    path: Path,
    *,
    base: str = "master",
    stale_days: int = 14,
    now: int | None = None,
) -> tuple[Path, list[BranchStatus]]:
    if stale_days < 0:
        raise SystemExit("--stale-days must be non-negative")

    root = find_git_root(path)
    if root is None:
        raise SystemExit(f"not inside a Git repository: {path}")
    if (
        _git(
            root,
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{base}^{{commit}}",
            check=False,
        ).returncode
        != 0
    ):
        raise SystemExit(f"base ref does not exist: {base}")

    checked_out = _worktrees(root)
    current_time = int(time.time()) if now is None else now
    branches: list[BranchStatus] = []
    for name, last_commit in _branches(root):
        if name == base:
            continue

        ahead, behind = _ahead_behind(root, base, name)
        worktree = checked_out.get(name)
        dirty = worktree is not None and bool(
            _git(worktree, "status", "--porcelain=v1").stdout
        )

        if ahead == 0:
            status, merge = "MERGED", "—"
        elif behind > 0 and _absorbed(root, base, name):
            status, merge = "ABSORBED", "—"
        else:
            merge = _mergeability(root, base, name, behind)
            status = "CONFLICT" if merge == "conflict" else "READY"

        branches.append(
            BranchStatus(
                name,
                ahead,
                behind,
                last_commit,
                status,
                merge,
                current_time - last_commit >= stale_days * 86400,
                worktree,
                dirty,
            )
        )

    branches.sort(
        key=lambda branch: (
            _STATUS_ORDER[branch.status],
            -branch.last_commit,
            branch.name,
        )
    )
    return root, branches


def _age(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d" if days < 60 else f"{days // 30}mo"


def _dashboard(
    root: Path,
    branches: list[BranchStatus],
    *,
    base: str,
    now: int,
    watch_interval: float | None = None,
) -> Group:
    header = Text.assemble(
        ("Branch status", "bold"),
        ("  base ", "dim"),
        (base, "bold cyan"),
        ("  ", "dim"),
        (str(root), "dim"),
    )
    if watch_interval is not None:
        header.append(
            f"  watching every {watch_interval:g}s · Ctrl-C to stop",
            style="dim",
        )

    table = Table(box=None, pad_edge=False)
    table.add_column("Status", no_wrap=True)
    table.add_column("Branch", overflow="ellipsis", no_wrap=True)
    table.add_column("Ahead", justify="right")
    table.add_column("Behind", justify="right")
    table.add_column("Merge", no_wrap=True)
    table.add_column("Last active", justify="right", no_wrap=True)
    table.add_column("Worktree", overflow="ellipsis", no_wrap=True)

    mergeable = conflicts = cleanup = stale = live = 0
    previous: str | None = None
    for branch in branches:
        if previous is not None and branch.status != previous:
            table.add_section()

        activity = Text(_age(now - branch.last_commit))
        if branch.stale:
            activity.append(" stale", style="bold yellow")
            stale += 1

        merge = Text(branch.merge)
        if branch.merge in {"ff", "clean"}:
            merge.stylize("green")
            mergeable += 1
        elif branch.merge == "conflict":
            merge.stylize("bold red")
            conflicts += 1

        worktree = Text("—", style="dim")
        if branch.worktree is not None:
            live += 1
            state = "dirty" if branch.dirty else "clean"
            style = "bold red" if branch.dirty else "cyan"
            worktree = Text.assemble(
                (state, style), (" · ", "dim"), str(branch.worktree)
            )

        if branch.status in {"MERGED", "ABSORBED"}:
            cleanup += 1
        table.add_row(
            Text(branch.status, style=_STATUS_STYLE[branch.status]),
            Text(branch.name),
            str(branch.ahead),
            str(branch.behind),
            merge,
            activity,
            worktree,
        )
        previous = branch.status

    footer = Text(
        f"{mergeable} mergeable · {conflicts} conflicts · {cleanup} cleanup"
        f" · {stale} stale · {live} checked out",
        style="dim",
    )
    return Group(header, table, footer)


def _snapshot(
    path: Path,
    *,
    base: str,
    stale_days: int,
    watch_interval: float | None = None,
) -> Group:
    now = int(time.time())
    root, branches = collect_branch_status(
        path, base=base, stale_days=stale_days, now=now
    )
    return _dashboard(
        root,
        branches,
        base=base,
        now=now,
        watch_interval=watch_interval,
    )


def print_branch_status(
    path: Path = Path("."),
    *,
    base: str = "master",
    stale_days: int = 14,
    watch: bool = False,
    interval: float = 2.0,
) -> None:
    if interval <= 0:
        raise SystemExit("--interval must be positive")

    console = Console()
    if not watch:
        console.print(_snapshot(path, base=base, stale_days=stale_days))
        return

    with Live(
        _snapshot(
            path,
            base=base,
            stale_days=stale_days,
            watch_interval=interval,
        ),
        console=console,
        auto_refresh=False,
    ) as live:
        try:
            while True:
                time.sleep(interval)
                live.update(
                    _snapshot(
                        path,
                        base=base,
                        stale_days=stale_days,
                        watch_interval=interval,
                    ),
                    refresh=True,
                )
        except KeyboardInterrupt:
            return
