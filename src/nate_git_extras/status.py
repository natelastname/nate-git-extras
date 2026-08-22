"""Read-only branch status dashboard."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import termios
import threading
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

_STATUS_STYLE = {
    "READY": "bold green",
    "CONFLICT": "bold red",
    "MERGED": "green",
    "ABSORBED": "cyan",
}
_WATCH_POLL_SECONDS = 0.01
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


@dataclass(slots=True)
class FetchStatus:
    process: subprocess.Popen[str] | None = None
    state: str = "idle"
    detail: str = ""
    finished_at: float | None = None


@dataclass(slots=True)
class ScanStatus:
    thread: threading.Thread | None = None
    result: tuple[Path, list[BranchStatus]] | None = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    mergeable: int
    conflicts: int
    cleanup: int
    stale: int
    live: int
    remote: int


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
    local = _git(
        root,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:unix)\t%(objectname)",
        "refs/heads/",
    )
    refs: list[tuple[str, int, bool]] = []
    local_shas: dict[str, str] = {}
    for line in local.stdout.splitlines():
        if not line:
            continue
        name, timestamp, sha = line.split("\t", 2)
        refs.append((name, int(timestamp), False))
        local_shas[name] = sha

    if not include_remotes:
        return refs

    remote = _git(
        root,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:unix)\t%(objectname)\t%(symref)",
        "refs/remotes/",
    )
    for line in remote.stdout.splitlines():
        if not line:
            continue
        name, timestamp, sha, symref = line.split("\t", 3)
        if symref:
            continue
        local_name = name.split("/", 1)[1] if "/" in name else name
        if local_shas.get(local_name) == sha:
            continue
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

    branches.sort(key=lambda branch: (branch.remote, -branch.last_commit, branch.name))
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


def _selected_index(branches: list[BranchStatus], selected: str | None) -> int:
    if not branches or selected is None:
        return 0
    for index, branch in enumerate(branches):
        if branch.name == selected:
            return index
    return 0


def _move_selection(
    branches: list[BranchStatus], selected: str | None, delta: int
) -> str | None:
    if not branches:
        return None
    index = _selected_index(branches, selected)
    index = max(0, min(len(branches) - 1, index + delta))
    return branches[index].name


def _summary(branches: list[BranchStatus]) -> DashboardSummary:
    return DashboardSummary(
        mergeable=sum(branch.merge in {"ff", "clean"} for branch in branches),
        conflicts=sum(branch.merge == "conflict" for branch in branches),
        cleanup=sum(
            not branch.remote and branch.status in {"MERGED", "ABSORBED"}
            for branch in branches
        ),
        stale=sum(branch.stale for branch in branches),
        live=sum(branch.worktree is not None for branch in branches),
        remote=sum(branch.remote for branch in branches),
    )


def _fetch_message(fetch: FetchStatus, now: float) -> Text | None:
    if fetch.state == "fetching":
        return Text("fetching…", style="bold yellow")
    if fetch.state == "failed":
        return Text(f"fetch failed: {fetch.detail}", style="bold red")
    if fetch.state == "ok" and fetch.finished_at is not None:
        return Text(f"fetch ok {_age(int(now - fetch.finished_at))} ago", style="green")
    return None


def _header(
    root: Path,
    base: str,
    *,
    watch: bool,
    remotes_visible: bool,
    fetch_interval: float | None,
) -> Text:
    header = Text.assemble(
        ("Branch status", "bold"),
        ("  base ", "dim"),
        (base, "bold cyan"),
        ("  ", "dim"),
        (str(root), "dim"),
    )
    if watch:
        header.append("  ↑/↓ select · g fetch remotes · q / Ctrl-C stop", style="dim")
        if remotes_visible:
            header.append(" · remotes shown", style="cyan")
        if fetch_interval is not None:
            header.append(f" · fetch every {fetch_interval:g}s", style="dim")
    return header


def _footer(
    summary: DashboardSummary,
    *,
    remotes_visible: bool,
    fetch: FetchStatus | None,
    refreshing: bool = False,
) -> Text:
    footer = Text(
        f"{summary.mergeable} mergeable · {summary.conflicts} conflicts"
        f" · {summary.cleanup} cleanup · {summary.stale} stale"
        f" · {summary.live} checked out",
        style="dim",
    )
    if remotes_visible:
        footer.append(f" · {summary.remote} remote", style="dim cyan")
    if refreshing:
        footer.append(" · refreshing…", style="dim yellow")
    if fetch is not None:
        message = _fetch_message(fetch, time.time())
        if message is not None:
            footer.append(" · ", style="dim")
            footer.append_text(message)
    return footer


def _static_dashboard(
    root: Path,
    branches: list[BranchStatus],
    *,
    base: str,
    now: int,
) -> Group:
    table = Table(box=None, pad_edge=False)
    table.add_column("Status", no_wrap=True)
    table.add_column("Branch", overflow="ellipsis", no_wrap=True)
    table.add_column("Ahead", justify="right")
    table.add_column("Behind", justify="right")
    table.add_column("Merge", no_wrap=True)
    table.add_column("Last active", justify="right", no_wrap=True)
    table.add_column("Worktree", overflow="ellipsis", no_wrap=True)

    for branch in branches:
        activity = Text(_age(now - branch.last_commit))
        if branch.stale:
            activity.append(" stale", style="bold yellow")

        merge = Text(branch.merge)
        if branch.merge in {"ff", "clean"}:
            merge.stylize("green")
        elif branch.merge == "conflict":
            merge.stylize("bold red")

        worktree = Text("—", style="dim")
        if branch.worktree is not None:
            state = "dirty" if branch.dirty else "clean"
            style = "bold red" if branch.dirty else "cyan"
            worktree = Text.assemble(
                (state, style), (" · ", "dim"), str(branch.worktree)
            )

        table.add_row(
            Text(branch.status, style=_STATUS_STYLE[branch.status]),
            Text(branch.name, style="cyan" if branch.remote else ""),
            str(branch.ahead),
            str(branch.behind),
            merge,
            activity,
            worktree,
        )

    return Group(
        _header(root, base, watch=False, remotes_visible=False, fetch_interval=None),
        table,
        _footer(_summary(branches), remotes_visible=False, fetch=None),
    )


def _fit(value: str, width: int, *, right: bool = False) -> str:
    if width <= 0:
        return ""
    if len(value) > width:
        value = value[: max(0, width - 1)] + "…"
    return value.rjust(width) if right else value.ljust(width)


def _watch_widths(width: int) -> tuple[int, int]:
    fixed = 8 + 1 + 5 + 1 + 6 + 1 + 8 + 1 + 11 + 1
    available = max(16, width - fixed)
    branch = min(40, max(16, available // 2))
    worktree = max(0, available - branch)
    return branch, worktree


def _watch_table_header(width: int) -> Text:
    branch_width, worktree_width = _watch_widths(width)
    line = Text()
    line.append(_fit("Status", 8), style="bold")
    line.append(" ")
    line.append(_fit("Branch", branch_width), style="bold")
    line.append(" ")
    line.append(_fit("Ahead", 5, right=True), style="bold")
    line.append(" ")
    line.append(_fit("Behind", 6, right=True), style="bold")
    line.append(" ")
    line.append(_fit("Merge", 8), style="bold")
    line.append(" ")
    line.append(_fit("Last active", 11, right=True), style="bold")
    if worktree_width:
        line.append(" ")
        line.append(_fit("Worktree", worktree_width), style="bold")
    return line


def _watch_rows(branches: list[BranchStatus], now: int, width: int) -> list[Text]:
    branch_width, worktree_width = _watch_widths(width)
    rows: list[Text] = []
    for branch in branches:
        age = _age(now - branch.last_commit)
        if branch.stale:
            age += " stale"
        worktree = "—"
        if branch.worktree is not None:
            state = "dirty" if branch.dirty else "clean"
            worktree = f"{state} · {branch.worktree}"

        row = Text()
        row.append(_fit(branch.status, 8), style=_STATUS_STYLE[branch.status])
        row.append(" ")
        row.append(
            _fit(branch.name, branch_width),
            style="cyan" if branch.remote else "",
        )
        row.append(" ")
        row.append(_fit(str(branch.ahead), 5, right=True))
        row.append(" ")
        row.append(_fit(str(branch.behind), 6, right=True))
        row.append(" ")
        merge_style = "green" if branch.merge in {"ff", "clean"} else ""
        if branch.merge == "conflict":
            merge_style = "bold red"
        row.append(_fit(branch.merge, 8), style=merge_style)
        row.append(" ")
        age_style = "bold yellow" if branch.stale else ""
        row.append(_fit(age, 11, right=True), style=age_style)
        if worktree_width:
            row.append(" ")
            worktree_style = "dim"
            if branch.worktree is not None:
                worktree_style = "bold red" if branch.dirty else "cyan"
            row.append(_fit(worktree, worktree_width), style=worktree_style)
        rows.append(row)
    return rows


def _watch_layout(
    root: Path,
    rows: list[Text],
    summary: DashboardSummary,
    *,
    base: str,
    selected_index: int,
    width: int,
    height: int,
    remotes_visible: bool,
    fetch_interval: float | None,
    fetch: FetchStatus,
    refreshing: bool,
) -> Layout:
    limit = max(1, height - 3)
    if len(rows) <= limit:
        start = 0
    else:
        start = max(0, min(selected_index - limit // 2, len(rows) - limit))

    visible: list[Text] = []
    for index in range(start, min(start + limit, len(rows))):
        row = rows[index]
        if index == selected_index:
            row = row.copy()
            row.stylize("reverse")
        visible.append(row)

    content = Group(
        _header(
            root,
            base,
            watch=True,
            remotes_visible=remotes_visible,
            fetch_interval=fetch_interval,
        ),
        _watch_table_header(width),
        *visible,
    )
    layout = Layout()
    layout.split_column(
        Layout(content, name="content"),
        Layout(
            _footer(
                summary,
                remotes_visible=remotes_visible,
                fetch=fetch,
                refreshing=refreshing,
            ),
            name="footer",
            size=1,
        ),
    )
    return layout


def _snapshot(
    path: Path,
    *,
    base: str,
    stale_days: int,
    include_remotes: bool = False,
) -> tuple[Path, list[BranchStatus]]:
    return collect_branch_status(
        path,
        base=base,
        stale_days=stale_days,
        now=int(time.time()),
        include_remotes=include_remotes,
    )


def _start_scan(
    path: Path,
    *,
    base: str,
    stale_days: int,
    include_remotes: bool,
    scan: ScanStatus,
) -> bool:
    if scan.thread is not None:
        return False

    scan.result = None
    scan.error = None

    def run() -> None:
        try:
            scan.result = _snapshot(
                path,
                base=base,
                stale_days=stale_days,
                include_remotes=include_remotes,
            )
        except BaseException as exc:
            scan.error = exc

    scan.thread = threading.Thread(target=run, daemon=True)
    scan.thread.start()
    return True


def _poll_scan(scan: ScanStatus) -> tuple[Path, list[BranchStatus]] | None:
    thread = scan.thread
    if thread is None or thread.is_alive():
        return None

    thread.join()
    scan.thread = None
    if scan.error is not None:
        error = scan.error
        scan.error = None
        raise error

    result = scan.result
    scan.result = None
    return result


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

    first = os.read(fd, 1)
    if first != b"\x1b":
        return first.decode(errors="ignore").lower()

    sequence = first
    for _ in range(2):
        readable, _, _ = select.select([fd], [], [], 0.002)
        if not readable:
            break
        sequence += os.read(fd, 1)
    if sequence in {b"\x1b[A", b"\x1bOA"}:
        return "up"
    if sequence in {b"\x1b[B", b"\x1bOB"}:
        return "down"
    return None


def _start_fetch(root: Path, fetch: FetchStatus) -> bool:
    if fetch.process is not None:
        return False
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    fetch.process = subprocess.Popen(
        ["git", "-C", str(root), "fetch", "--all", "--prune"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    fetch.state = "fetching"
    fetch.detail = ""
    return True


def _poll_fetch(fetch: FetchStatus) -> bool:
    process = fetch.process
    if process is None or process.poll() is None:
        return False

    stdout, stderr = process.communicate()
    fetch.process = None
    fetch.finished_at = time.time()
    if process.returncode == 0:
        fetch.state = "ok"
        fetch.detail = ""
        return True

    lines = (stderr.strip() or stdout.strip()).splitlines()
    detail = lines[-1] if lines else f"exit {process.returncode}"
    for line in reversed(lines):
        if line.startswith(("fatal:", "error:")):
            detail = line
            break
    fetch.state = "failed"
    fetch.detail = detail[:160]
    return True


def _stop_fetch(fetch: FetchStatus) -> None:
    if fetch.process is None:
        return
    fetch.process.terminate()
    fetch.process.wait()
    fetch.process = None


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
    root, branches = _snapshot(
        path,
        base=base,
        stale_days=stale_days,
        include_remotes=watch and interval is not None,
    )
    if not watch:
        console.print(
            _static_dashboard(root, branches, base=base, now=int(time.time()))
        )
        return

    fetch = FetchStatus()
    scan = ScanStatus()
    include_remotes = interval is not None
    selected_index = 0
    rows = _watch_rows(branches, int(time.time()), console.width)
    summary = _summary(branches)

    last_fetch = time.monotonic()
    if interval is not None:
        _start_fetch(root, fetch)
    next_scan = time.monotonic() + _WATCH_REFRESH_SECONDS

    def render() -> Layout:
        return _watch_layout(
            root,
            rows,
            summary,
            base=base,
            selected_index=selected_index,
            width=console.width,
            height=console.height,
            remotes_visible=include_remotes,
            fetch_interval=interval,
            fetch=fetch,
            refreshing=scan.thread is not None,
        )

    with _watch_terminal(console) as fd:
        with Live(render(), console=console, screen=True, auto_refresh=False) as live:
            try:
                while True:
                    key = _watch_key(fd, _WATCH_POLL_SECONDS)
                    if key == "q":
                        return
                    if key in {"up", "down"}:
                        delta = -1 if key == "up" else 1
                        selected_index = (
                            max(0, min(len(branches) - 1, selected_index + delta))
                            if branches
                            else 0
                        )
                        live.update(render(), refresh=True)
                        continue
                    if key == "g":
                        include_remotes = True
                        if _start_fetch(root, fetch):
                            last_fetch = time.monotonic()
                        live.update(render(), refresh=True)
                        continue

                    now = time.monotonic()
                    fetch_changed = _poll_fetch(fetch)
                    if fetch_changed:
                        last_fetch = now
                        next_scan = 0.0

                    scan_result = _poll_scan(scan)
                    if scan_result is not None:
                        selected_name = (
                            branches[selected_index].name if branches else None
                        )
                        root, branches = scan_result
                        selected_index = _selected_index(branches, selected_name)
                        rows = _watch_rows(branches, int(time.time()), console.width)
                        summary = _summary(branches)
                        next_scan = now + _WATCH_REFRESH_SECONDS
                        live.update(render(), refresh=True)
                        continue

                    if (
                        interval is not None
                        and fetch.process is None
                        and now - last_fetch >= interval
                    ):
                        _start_fetch(root, fetch)
                        last_fetch = now
                        live.update(render(), refresh=True)
                        continue

                    if (
                        fetch.process is None
                        and scan.thread is None
                        and now >= next_scan
                    ):
                        _start_scan(
                            path,
                            base=base,
                            stale_days=stale_days,
                            include_remotes=include_remotes,
                            scan=scan,
                        )
                        next_scan = now + _WATCH_REFRESH_SECONDS
                        live.update(render(), refresh=True)
            except KeyboardInterrupt:
                return
            finally:
                _stop_fetch(fetch)
