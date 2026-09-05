"""Remote-centric live commit feed."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

from .commit_detail import (
    CommitDetail,
    collect_commit_detail,
    commit_detail_layout,
    commit_patch,
    commit_patch_layout,
)
from .git_utils import find_git_root
from .recent import RecentCommit, _read_key, _watch_header, _watch_rows
from .status import FetchStatus, _age, _poll_fetch, _stop_fetch, _watch_terminal

_REFRESH_SECONDS = 2.0
_POLL_SECONDS = 0.01


@dataclass(slots=True)
class ScanStatus:
    thread: threading.Thread | None = None
    result: tuple[Path, list[RecentCommit]] | None = None
    error: BaseException | None = None


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


def _require_remote(root: Path, remote: str) -> None:
    if _git(root, "remote", "get-url", remote, check=False).returncode != 0:
        raise SystemExit(f"remote does not exist: {remote}")


def _branch_names(root: Path, remote: str, shas: list[str]) -> dict[str, str]:
    if not shas:
        return {}

    exact: dict[str, str] = {}
    refs = _git(
        root,
        "for-each-ref",
        "--format=%(objectname)\t%(refname:short)\t%(symref)",
        f"refs/remotes/{remote}/",
    )
    for line in refs.stdout.splitlines():
        if not line:
            continue
        sha, name, symref = line.split("\t", 2)
        if not symref:
            exact.setdefault(sha, name.removeprefix(f"{remote}/"))

    result = _git(
        root,
        "name-rev",
        "--name-only",
        f"--refs=refs/remotes/{remote}/*",
        *shas,
        check=False,
    )
    resolved = result.stdout.splitlines() if result.returncode == 0 else []

    names: dict[str, str] = {}
    for index, sha in enumerate(shas):
        if sha in exact:
            names[sha] = exact[sha]
            continue
        name = resolved[index].strip() if index < len(resolved) else ""
        if not name or name == "undefined":
            names[sha] = "—"
            continue
        name = re.split(r"[~^]", name, maxsplit=1)[0]
        name = name.removeprefix("remotes/").removeprefix(f"{remote}/")
        names[sha] = name
    return names


def collect_remote_commits(
    path: Path,
    remote: str,
    *,
    limit: int = 50,
) -> tuple[Path, list[RecentCommit]]:
    """Collect the newest commits reachable from one remote's tracking refs."""
    if limit <= 0:
        raise SystemExit("--limit must be positive")
    root = find_git_root(path)
    if root is None:
        raise SystemExit(f"not inside a Git repository: {path}")
    _require_remote(root, remote)

    sample = max(100, limit * 5)
    output = _git(
        root,
        "log",
        f"--remotes={remote}",
        f"--max-count={sample}",
        "--format=%H%x1f%ct%x1f%s%x1e",
    ).stdout
    raw: list[tuple[str, int, str]] = []
    for record in output.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, timestamp, summary = record.split("\x1f", 2)
        raw.append((sha, int(timestamp), summary))

    raw.sort(key=lambda commit: (-commit[1], commit[0]))
    raw = raw[:limit]
    branches = _branch_names(root, remote, [sha for sha, _, _ in raw])
    return root, [
        RecentCommit(sha, timestamp, branches.get(sha, "—"), summary)
        for sha, timestamp, summary in raw
    ]


def _start_fetch(root: Path, remote: str, fetch: FetchStatus) -> bool:
    if fetch.process is not None:
        return False
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    fetch.process = subprocess.Popen(
        ["git", "-C", str(root), "fetch", remote, "--prune"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    fetch.state = "fetching"
    fetch.detail = ""
    return True


def _start_scan(path: Path, remote: str, limit: int, scan: ScanStatus) -> bool:
    if scan.thread is not None:
        return False
    scan.result = None
    scan.error = None

    def run() -> None:
        try:
            scan.result = collect_remote_commits(path, remote, limit=limit)
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
    remote: str,
    rows: list[Text],
    *,
    selected: int,
    total: int,
    width: int,
    height: int,
    interval: float | None,
    fetch: FetchStatus,
    refreshing: bool,
    following: bool,
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
        ("Watching ", "bold"),
        (remote, "bold cyan"),
        ("  ", "dim"),
        (str(root), "dim"),
    )
    title.append(
        "  ↑/↓ select · f follow newest · Enter detail · g fetch · q / Ctrl-C stop",
        style="dim",
    )
    if interval is not None:
        title.append(f" · fetch every {interval:g}s", style="dim")

    footer = Text(f"{total} commits", style="dim")
    if following:
        footer.append(" · following newest", style="bold cyan")
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


def watch_remote(
    remote: str,
    path: Path = Path("."),
    *,
    limit: int = 50,
    interval: float | None = None,
    base: str = "master",
    follow: bool = False,
) -> None:
    """Watch commits reachable from a single remote's tracking refs."""
    if limit <= 0:
        raise SystemExit("--limit must be positive")
    if interval is not None and interval <= 0:
        raise SystemExit("--interval must be positive")

    root, commits = collect_remote_commits(path, remote, limit=limit)
    console = Console()
    fetch = FetchStatus()
    scan = ScanStatus()
    selected = 0
    following = follow
    view = "feed"
    detail: CommitDetail | None = None
    selected_file = 0
    patch = ""
    patch_offset = 0
    rows = _watch_rows(commits, int(time.time()), console.width)
    last_fetch = time.monotonic()
    next_refresh = time.monotonic() + _REFRESH_SECONDS
    if interval is not None:
        _start_fetch(root, remote, fetch)

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
            remote,
            rows,
            selected=selected,
            total=len(commits),
            width=console.width,
            height=console.height,
            interval=interval,
            fetch=fetch,
            refreshing=scan.thread is not None,
            following=following,
        )

    with _watch_terminal(console) as fd:
        with Live(render(), console=console, screen=True, auto_refresh=False) as live:
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
                            following = False
                            delta = -1 if key == "up" else 1
                            selected = (
                                max(0, min(len(commits) - 1, selected + delta))
                                if commits
                                else 0
                            )
                            live.update(render(), refresh=True)
                            continue
                        if key == "f":
                            following = not following
                            if following:
                                selected = 0
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
                            if _start_fetch(root, remote, fetch):
                                last_fetch = time.monotonic()
                            live.update(render(), refresh=True)
                            continue

                    now = time.monotonic()
                    if _poll_fetch(fetch):
                        last_fetch = now
                        if fetch.state == "ok" and scan.thread is None:
                            _start_scan(path, remote, limit, scan)
                        live.update(render(), refresh=True)

                    scan_result = _poll_scan(scan)
                    if scan_result is not None:
                        root, scanned = scan_result
                        selected_sha = commits[selected].sha if commits else None
                        commits = scanned
                        selected = (
                            0
                            if following
                            else next(
                                (
                                    index
                                    for index, commit in enumerate(commits)
                                    if commit.sha == selected_sha
                                ),
                                0,
                            )
                        )
                        rows = _watch_rows(commits, int(time.time()), console.width)
                        live.update(render(), refresh=True)
                        next_refresh = now + _REFRESH_SECONDS
                        continue

                    if (
                        interval is not None
                        and fetch.process is None
                        and now - last_fetch >= interval
                    ):
                        if _start_fetch(root, remote, fetch):
                            last_fetch = now
                            live.update(render(), refresh=True)
                        continue

                    if scan.thread is None and now >= next_refresh:
                        _start_scan(path, remote, limit, scan)
                        next_refresh = now + _REFRESH_SECONDS
                        continue
            finally:
                _stop_fetch(fetch)
