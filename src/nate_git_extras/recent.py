"""Recent commit feed."""

from __future__ import annotations

import os
import select
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.text import Text

from .commit_detail import (
    CommitDetail,
    branch_for_commit,
    collect_commit_detail,
    commit_detail_layout,
    commit_patch,
    commit_patch_layout,
)
from .git_utils import find_git_root
from .status import (
    FetchStatus,
    _age,
    _fit,
    _poll_fetch,
    _start_fetch,
    _stop_fetch,
    _watch_terminal,
)

_REFRESH_SECONDS = 2.0
_POLL_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class RecentCommit:
    sha: str
    timestamp: int
    branch: str
    summary: str
    remote: bool = False


@dataclass(slots=True)
class ScanStatus:
    thread: threading.Thread | None = None
    result: tuple[Path, list[RecentCommit]] | None = None
    error: BaseException | None = None
    include_remotes: bool = False


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


def _log(
    root: Path, *, include_remotes: bool, limit: int
) -> list[tuple[str, int, str]]:
    args = [
        "log",
        "--branches",
        f"--max-count={limit}",
        "--format=%H%x1f%ct%x1f%s%x1e",
    ]
    if include_remotes:
        args.insert(2, "--remotes")
    output = _git(root, *args).stdout
    commits: list[tuple[str, int, str]] = []
    for record in output.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, timestamp, summary = record.split("\x1f", 2)
        commits.append((sha, int(timestamp), summary))
    commits.sort(key=lambda commit: (-commit[1], commit[0]))
    return commits[:limit]


def collect_recent_commits(
    path: Path,
    *,
    limit: int = 20,
    include_remotes: bool = False,
) -> tuple[Path, list[RecentCommit]]:
    if limit <= 0:
        raise SystemExit("--limit must be positive")
    root = find_git_root(path)
    if root is None:
        raise SystemExit(f"not inside a Git repository: {path}")

    commits: list[RecentCommit] = []
    for sha, timestamp, summary in _log(
        root, include_remotes=include_remotes, limit=limit
    ):
        branch, remote = branch_for_commit(
            root, sha, include_remotes=include_remotes
        )
        commits.append(RecentCommit(sha, timestamp, branch, summary, remote))
    return root, commits


def _static_dashboard(root: Path, commits: list[RecentCommit], *, now: int) -> Group:
    table = Table(box=None, pad_edge=False)
    table.add_column("Age", justify="right", no_wrap=True)
    table.add_column("Commit", no_wrap=True)
    table.add_column("Branch", overflow="ellipsis", no_wrap=True)
    table.add_column("Summary", overflow="ellipsis", no_wrap=True)
    for commit in commits:
        table.add_row(
            _age(now - commit.timestamp),
            Text(commit.sha[:7], style="bold cyan"),
            Text(commit.branch, style="cyan" if commit.remote else ""),
            commit.summary,
        )
    return Group(
        Text.assemble(
            ("Recent commits", "bold"),
            ("  ", "dim"),
            (str(root), "dim"),
        ),
        table,
        Text(f"showing {len(commits)} commits", style="dim"),
    )


def _watch_rows(commits: list[RecentCommit], now: int, width: int) -> list[Text]:
    age_width, sha_width = 7, 7
    branch_width = min(38, max(16, (width - 18) // 3))
    summary_width = max(12, width - age_width - sha_width - branch_width - 3)
    rows: list[Text] = []
    for commit in commits:
        row = Text()
        row.append(_fit(_age(now - commit.timestamp), age_width, right=True))
        row.append(" ")
        row.append(_fit(commit.sha[:7], sha_width), style="bold cyan")
        row.append(" ")
        row.append(
            _fit(commit.branch, branch_width),
            style="cyan" if commit.remote else "",
        )
        row.append(" ")
        row.append(_fit(commit.summary, summary_width))
        rows.append(row)
    return rows


def _watch_header(width: int) -> Text:
    age_width, sha_width = 7, 7
    branch_width = min(38, max(16, (width - 18) // 3))
    summary_width = max(12, width - age_width - sha_width - branch_width - 3)
    return Text.assemble(
        (_fit("Age", age_width, right=True), "bold"),
        " ",
        (_fit("Commit", sha_width), "bold"),
        " ",
        (_fit("Branch", branch_width), "bold"),
        " ",
        (_fit("Summary", summary_width), "bold"),
    )


def _fetch_text(fetch: FetchStatus) -> Text | None:
    if fetch.state == "fetching":
        return Text("fetching…", style="bold yellow")
    if fetch.state == "failed":
        return Text(f"fetch failed: {fetch.detail}", style="bold red")
    if fetch.state == "ok" and fetch.finished_at is not None:
        age = _age(int(time.time() - fetch.finished_at))
        return Text(f"fetch ok {age} ago", style="green")
    return None


def _feed_layout(
    root: Path,
    rows: list[Text],
    *,
    total: int,
    selected: int,
    width: int,
    height: int,
    remotes_loaded: bool,
    interval: float | None,
    fetch: FetchStatus,
    refreshing: bool,
) -> Layout:
    limit = max(1, height - 3)
    start = (
        0
        if len(rows) <= limit
        else max(0, min(selected - limit // 2, len(rows) - limit))
    )
    visible: list[Text] = []
    for index in range(start, min(start + limit, len(rows))):
        row = rows[index]
        if index == selected:
            row = row.copy()
            row.stylize("reverse")
        visible.append(row)

    title = Text.assemble(
        ("Recent commits", "bold"),
        ("  ", "dim"),
        (str(root), "dim"),
    )
    title.append(
        "  ↑/↓ select · Enter detail · g fetch remotes · q / Ctrl-C stop",
        style="dim",
    )
    if remotes_loaded:
        title.append(" · remote snapshot shown", style="cyan")
    if interval is not None:
        title.append(f" · fetch every {interval:g}s", style="dim")

    footer = Text(f"showing {total} commits", style="dim")
    if refreshing:
        footer.append(" · refreshing…", style="dim yellow")
    fetch_text = _fetch_text(fetch)
    if fetch_text is not None:
        footer.append(" · ", style="dim")
        footer.append_text(fetch_text)

    layout = Layout()
    layout.split_column(
        Layout(Group(title, _watch_header(width), *visible)),
        Layout(footer, size=1),
    )
    return layout


def _start_scan(
    path: Path,
    *,
    limit: int,
    include_remotes: bool,
    scan: ScanStatus,
) -> bool:
    if scan.thread is not None:
        return False
    scan.result = None
    scan.error = None
    scan.include_remotes = include_remotes

    def run() -> None:
        try:
            scan.result = collect_recent_commits(
                path,
                limit=limit,
                include_remotes=include_remotes,
            )
        except BaseException as exc:
            scan.error = exc

    scan.thread = threading.Thread(target=run, daemon=True)
    scan.thread.start()
    return True


def _poll_scan(scan: ScanStatus) -> tuple[Path, list[RecentCommit]] | None:
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


def _fetch_once(root: Path) -> None:
    fetch = FetchStatus()
    _start_fetch(root, fetch)
    while not _poll_fetch(fetch):
        time.sleep(0.01)
    if fetch.state == "failed":
        raise SystemExit(f"fetch failed: {fetch.detail}")


def _read_key(fd: int, timeout: float) -> str | None:
    readable, _, _ = select.select([fd], [], [], timeout)
    if not readable:
        return None

    first = os.read(fd, 1)
    if first in {b"\r", b"\n"}:
        return "enter"
    if first != b"\x1b":
        return first.decode(errors="ignore").lower()

    sequence = first
    for _ in range(2):
        readable, _, _ = select.select([fd], [], [], 0.002)
        if not readable:
            break
        sequence += os.read(fd, 1)
    if sequence == b"\x1b":
        return "esc"
    if sequence in {b"\x1b[A", b"\x1bOA"}:
        return "up"
    if sequence in {b"\x1b[B", b"\x1bOB"}:
        return "down"
    return None


def print_recent_commits(
    path: Path = Path("."),
    *,
    limit: int = 20,
    watch: bool = False,
    interval: float | None = None,
    fetch_first: bool = False,
    base: str = "master",
) -> None:
    if limit <= 0:
        raise SystemExit("--limit must be positive")
    if interval is not None and interval <= 0:
        raise SystemExit("--interval must be positive")
    if interval is not None and not watch:
        raise SystemExit("--interval requires --watch")

    root = find_git_root(path)
    if root is None:
        raise SystemExit(f"not inside a Git repository: {path}")
    include_remotes = fetch_first
    if fetch_first:
        _fetch_once(root)

    root, commits = collect_recent_commits(
        path,
        limit=limit,
        include_remotes=include_remotes,
    )
    console = Console()
    if not watch:
        console.print(_static_dashboard(root, commits, now=int(time.time())))
        return

    fetch = FetchStatus()
    remotes_loaded = include_remotes
    remote_snapshot: list[RecentCommit] = []
    if remotes_loaded:
        local_shas = {
            commit.sha
            for commit in collect_recent_commits(path, limit=limit)[1]
        }
        remote_snapshot = [
            commit for commit in commits if commit.sha not in local_shas
        ]
    local_commits = (
        collect_recent_commits(path, limit=limit)[1]
        if remotes_loaded
        else commits
    )

    scan = ScanStatus()
    remote_scan_pending = False
    selected = 0
    view = "feed"
    detail: CommitDetail | None = None
    selected_file = 0
    patch = ""
    patch_offset = 0
    last_fetch = time.monotonic()
    next_refresh = time.monotonic() + _REFRESH_SECONDS
    if interval is not None and not fetch_first:
        _start_fetch(root, fetch)

    def rebuild() -> list[RecentCommit]:
        by_sha = {commit.sha: commit for commit in remote_snapshot}
        for commit in local_commits:
            by_sha[commit.sha] = commit
        return sorted(
            by_sha.values(),
            key=lambda commit: (-commit.timestamp, commit.sha),
        )[:limit]

    commits = rebuild()
    rows = _watch_rows(commits, int(time.time()), console.width)

    def render() -> Layout:
        if view == "detail" and detail is not None:
            return commit_detail_layout(
                root,
                detail,
                selected_file=selected_file,
                width=console.width,
                height=console.height,
            )
        if view == "patch" and detail is not None and detail.files:
            return commit_patch_layout(
                root,
                detail,
                detail.files[selected_file],
                patch,
                offset=patch_offset,
                width=console.width,
                height=console.height,
            )
        return _feed_layout(
            root,
            rows,
            total=len(commits),
            selected=selected,
            width=console.width,
            height=console.height,
            remotes_loaded=remotes_loaded,
            interval=interval,
            fetch=fetch,
            refreshing=scan.thread is not None,
        )

    with _watch_terminal(console) as fd:
        with Live(
            render(),
            console=console,
            screen=True,
            auto_refresh=False,
        ) as live:
            try:
                while True:
                    key = _read_key(fd, _POLL_SECONDS)

                    if view == "patch":
                        if key in {"q", "esc"}:
                            view = "detail"
                            live.update(render(), refresh=True)
                            continue
                        if key in {"up", "down"}:
                            patch_lines = patch.splitlines() or [""]
                            patch_limit = max(1, console.height - 2)
                            max_offset = max(0, len(patch_lines) - patch_limit)
                            patch_offset = max(
                                0,
                                min(
                                    max_offset,
                                    patch_offset + (-1 if key == "up" else 1),
                                ),
                            )
                            live.update(render(), refresh=True)
                            continue
                    elif view == "detail":
                        if key in {"q", "esc"}:
                            view = "feed"
                            detail = None
                            next_refresh = 0
                            live.update(render(), refresh=True)
                            continue
                        if key in {"up", "down"} and detail is not None:
                            delta = -1 if key == "up" else 1
                            selected_file = (
                                max(
                                    0,
                                    min(
                                        len(detail.files) - 1,
                                        selected_file + delta,
                                    ),
                                )
                                if detail.files
                                else 0
                            )
                            live.update(render(), refresh=True)
                            continue
                        if (
                            key in {"enter", "d"}
                            and detail is not None
                            and detail.files
                        ):
                            patch = commit_patch(
                                root,
                                detail,
                                detail.files[selected_file],
                            )
                            patch_offset = 0
                            view = "patch"
                            live.update(render(), refresh=True)
                            continue
                    else:
                        if key == "q":
                            return
                        if key in {"up", "down"}:
                            delta = -1 if key == "up" else 1
                            selected = (
                                max(
                                    0,
                                    min(
                                        len(commits) - 1,
                                        selected + delta,
                                    ),
                                )
                                if commits
                                else 0
                            )
                            live.update(render(), refresh=True)
                            continue
                        if key == "enter" and commits:
                            _, detail = collect_commit_detail(
                                path,
                                commits[selected].sha,
                                base=base,
                            )
                            selected_file = 0
                            view = "detail"
                            live.update(render(), refresh=True)
                            continue
                        if key == "g":
                            if _start_fetch(root, fetch):
                                last_fetch = time.monotonic()
                            live.update(render(), refresh=True)
                            continue

                    now = time.monotonic()
                    if _poll_fetch(fetch):
                        last_fetch = now
                        if fetch.state == "ok":
                            remote_scan_pending = True
                        live.update(render(), refresh=True)

                    scan_kind = scan.include_remotes
                    scan_result = _poll_scan(scan)
                    if scan_result is not None:
                        _, scanned = scan_result
                        if scan_kind:
                            local_shas = {
                                commit.sha for commit in local_commits
                            }
                            remote_snapshot = [
                                commit
                                for commit in scanned
                                if commit.sha not in local_shas
                            ]
                            remotes_loaded = True
                            remote_scan_pending = False
                        else:
                            local_commits = scanned
                        selected_sha = (
                            commits[selected].sha if commits else None
                        )
                        commits = rebuild()
                        selected = next(
                            (
                                index
                                for index, commit in enumerate(commits)
                                if commit.sha == selected_sha
                            ),
                            0,
                        )
                        rows = _watch_rows(
                            commits,
                            int(time.time()),
                            console.width,
                        )
                        if view == "feed":
                            live.update(render(), refresh=True)
                        next_refresh = now + _REFRESH_SECONDS
                        continue

                    if view != "feed":
                        continue

                    if (
                        interval is not None
                        and fetch.process is None
                        and not remote_scan_pending
                        and now - last_fetch >= interval
                    ):
                        if _start_fetch(root, fetch):
                            last_fetch = now
                            live.update(render(), refresh=True)
                        continue

                    if (
                        remote_scan_pending
                        and fetch.process is None
                        and scan.thread is None
                    ):
                        _start_scan(
                            path,
                            limit=limit,
                            include_remotes=True,
                            scan=scan,
                        )
                        live.update(render(), refresh=True)
                        continue

                    if (
                        scan.thread is None
                        and not remote_scan_pending
                        and now >= next_refresh
                    ):
                        _start_scan(
                            path,
                            limit=limit,
                            include_remotes=False,
                            scan=scan,
                        )
                        next_refresh = now + _REFRESH_SECONDS
                        continue
            finally:
                _stop_fetch(fetch)
