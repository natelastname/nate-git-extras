"""Read-only branch status dashboard."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import termios
import time
import tty
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.layout import Layout
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
_WATCH_REFRESH_SECONDS = 2.0


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
    remote: bool = False


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


def _refs(root: Path, *, include_remotes: bool) -> list[tuple[str, int, bool]]:
    result = _git(
        root,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:unix)",
        "refs/heads/",
    )
    refs: list[tuple[str, int, bool]] = []
    for line in result.stdout.splitlines():
        if line:
            name, timestamp = line.split("\t", 1)
            refs.append((name, int(timestamp), False))

    if not include_remotes:
        return refs

    result = _git(
        root,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:unix)\t%(symref)",
        "refs/remotes/",
    )
    for line in result.stdout.splitlines():
        if not line:
            continue
        name, timestamp, symref = line.split("\t", 2)
        if not symref:
            refs.append((name, int(timestamp), True))
    return refs


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


def _fetch_remotes(root: Path) -> None:
    _git(root, "fetch", "--all", "--prune")


def collect_branch_status(
    path: Path,
    *,
    base: str = "master",
    stale_days: int = 14,
    now: int | None = None,
    include_remotes: bool = False,
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
    for name, last_commit, remote in _refs(root, include_remotes=include_remotes):
        if name == base:
            continue

        ahead, behind = _ahead_behind(root, base, name)
        worktree = None if remote else checked_out.get(name)
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
                remote,
            )
        )

    branches.sort(
        key=lambda branch: (
            _STATUS_ORDER[branch.status],
            branch.remote,
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
    watch: bool = False,
    remotes_visible: bool = False,
    fetch_interval: float | None = None,
) -> Group | Layout:
    header = Text.assemble(
        ("Branch status", "bold"),
        ("  base ", "dim"),
        (base, "bold cyan"),
        ("  ", "dim"),
        (str(root), "dim"),
    )
    if watch:
        header.append("  g fetch remotes · q / Ctrl-C to stop", style="dim")
        if remotes_visible:
            header.append(" · remotes shown", style="cyan")
        if fetch_interval is not None:
            header.append(f" · fetch every {fetch_interval:g}s", style="dim")

    table = Table(box=None, pad_edge=False)
    table.add_column("Status", no_wrap=True)
    table.add_column("Branch", overflow="ellipsis", no_wrap=True)
    table.add_column("Ahead", justify="right")
    table.add_column("Behind", justify="right")
    table.add_column("Merge", no_wrap=True)
    table.add_column("Last active", justify="right", no_wrap=True)
    table.add_column("Worktree", overflow="ellipsis", no_wrap=True)

    mergeable = conflicts = cleanup = stale = live = remote_count = 0
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

        if branch.remote:
            remote_count += 1
        elif branch.status in {"MERGED", "ABSORBED"}:
            cleanup += 1

        table.add_row(
            Text(branch.status, style=_STATUS_STYLE[branch.status]),
            Text(branch.name, style="cyan" if branch.remote else ""),
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
    if remotes_visible:
        footer.append(f" · {remote_count} remote", style="dim cyan")

    if not watch:
        return Group(header, table, footer)

    layout = Layout()
    layout.split_column(
        Layout(Group(header, table), name="content"),
        Layout(footer, name="footer", size=1),
    )
    return layout


def _snapshot(
    path: Path,
    *,
    base: str,
    stale_days: int,
    watch: bool = False,
    include_remotes: bool = False,
    fetch_interval: float | None = None,
) -> Group | Layout:
    now = int(time.time())
    root, branches = collect_branch_status(
        path,
        base=base,
        stale_days=stale_days,
        now=now,
        include_remotes=include_remotes,
    )
    return _dashboard(
        root,
        branches,
        base=base,
        now=now,
        watch=watch,
        remotes_visible=include_remotes,
        fetch_interval=fetch_interval,
    )


@contextmanager
def _watch_terminal(console: Console) -> Iterator[int]:
    if not console.is_terminal or not sys.stdin.isatty():
        raise SystemExit("--watch requires an interactive terminal")

    fd = sys.stdin.fileno()
    settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, settings)


def _watch_key(fd: int, timeout: float) -> str | None:
    readable, _, _ = select.select([fd], [], [], timeout)
    if not readable:
        return None
    return os.read(fd, 1).decode(errors="ignore").lower()


def print_branch_status(
    path: Path = Path("."),
    *,
    base: str = "master",
    stale_days: int = 14,
    watch: bool = False,
    interval: float | None = None,
) -> None:
    if interval is not None and interval <= 0:
        raise SystemExit("--interval must be positive")
    if interval is not None and not watch:
        raise SystemExit("--interval requires --watch")

    console = Console()
    if not watch:
        console.print(_snapshot(path, base=base, stale_days=stale_days))
        return

    root = find_git_root(path)
    if root is None:
        raise SystemExit(f"not inside a Git repository: {path}")

    include_remotes = interval is not None
    if include_remotes:
        _fetch_remotes(root)
    last_fetch = time.monotonic()
    poll_seconds = min(_WATCH_REFRESH_SECONDS, interval or _WATCH_REFRESH_SECONDS)

    with _watch_terminal(console) as fd:
        with Live(
            _snapshot(
                path,
                base=base,
                stale_days=stale_days,
                watch=True,
                include_remotes=include_remotes,
                fetch_interval=interval,
            ),
            console=console,
            screen=True,
            auto_refresh=False,
        ) as live:
            try:
                while True:
                    key = _watch_key(fd, poll_seconds)
                    if key == "q":
                        return

                    now = time.monotonic()
                    should_fetch = key == "g" or (
                        interval is not None and now - last_fetch >= interval
                    )
                    if should_fetch:
                        _fetch_remotes(root)
                        include_remotes = True
                        last_fetch = now

                    live.update(
                        _snapshot(
                            path,
                            base=base,
                            stale_days=stale_days,
                            watch=True,
                            include_remotes=include_remotes,
                            fetch_interval=interval,
                        ),
                        refresh=True,
                    )
            except KeyboardInterrupt:
                return
