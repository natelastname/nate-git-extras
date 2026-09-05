"""Command-line interface for nate-git-extras."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from loguru import logger
from rich.console import Console

from .commit_detail import print_commit_detail
from .cp import git_cp_many, git_cp_template
from .git_utils import find_git_root
from .ls import print_tree
from .recent import print_recent_commits
from .status import (
    FetchStatus,
    _poll_fetch,
    _start_fetch,
    _static_dashboard,
    collect_branch_status,
    print_branch_status,
)
from .watch import watch_remote

app = App(
    name="nate-git-extras",
    help="Small git-aware filesystem utilities.",
)

Verbose = Annotated[bool, Parameter(alias="-v")]
DryRun = Annotated[bool, Parameter(alias="-n")]


def _configure_action_logging(*, enabled: bool) -> None:
    if not enabled:
        return
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{message}")


def _print_fetched_status(path: Path, *, base: str, stale_days: int) -> None:
    root = find_git_root(path)
    if root is None:
        raise SystemExit(f"not inside a Git repository: {path}")

    fetch = FetchStatus()
    _start_fetch(root, fetch)
    while not _poll_fetch(fetch):
        time.sleep(0.01)
    if fetch.state == "failed":
        raise SystemExit(f"fetch failed: {fetch.detail}")

    now = int(time.time())
    root, branches = collect_branch_status(
        path,
        base=base,
        stale_days=stale_days,
        now=now,
        include_remotes=True,
    )
    Console().print(_static_dashboard(root, branches, base=base, now=now))


@app.command
def cp(
    src: list[Path],
    dst: Path,
    /,
    *,
    verbose: Verbose = False,
    dry_run: DryRun = False,
    template: bool = False,
) -> None:
    """Copy files or directories while respecting Git ignore rules.

    Parameters
    ----------
    src:
        Source path(s). Shell-expanded globs are supported. Multiple sources
        require an existing destination directory.
    dst:
        Destination path.
    verbose:
        Print every copied and skipped path.
    dry_run:
        Print what would happen without modifying the destination.
    template:
        Copy the contents of one source directory directly into the destination.
    """
    _configure_action_logging(enabled=verbose or dry_run)

    if template:
        if len(src) != 1:
            raise SystemExit("template mode expects exactly one source path")
        git_cp_template(src[0], dst, verbose=verbose, dry_run=dry_run)
        return

    git_cp_many(src, dst, verbose=verbose, dry_run=dry_run)


@app.command
def ls(
    path: Path = Path("."),
    /,
    *,
    include_ignored: bool = False,
    traverse_ignored: bool = False,
) -> None:
    """Print a tree-style directory listing.

    Parameters
    ----------
    path:
        File or directory to list.
    include_ignored:
        Show Git-ignored files and directories, but do not descend into ignored
        directories.
    traverse_ignored:
        Show and descend into Git-ignored directories. Implies include_ignored.
    """
    print_tree(
        path,
        include_ignored=include_ignored,
        traverse_ignored=traverse_ignored,
    )


@app.command
def status(
    path: Path = Path("."),
    /,
    *,
    base: str = "master",
    stale_days: int = 14,
    watch: bool = False,
    interval: float | None = None,
    fetch: bool = False,
) -> None:
    """Show branch merge and cleanup status relative to a base ref.

    Parameters
    ----------
    path:
        Repository path.
    base:
        Branch or commit-ish to compare against.
    stale_days:
        Mark branches whose tip has not moved in this many days as stale.
    watch:
        Continuously refresh until q or Ctrl-C. Use arrows to select, m to merge
        a READY branch, and g to fetch/display remote branches.
    interval:
        In watch mode, automatically fetch remotes every this many seconds.
    fetch:
        Fetch remotes once before printing a non-interactive remote-aware snapshot.
    """
    if fetch:
        if watch:
            raise SystemExit(
                "--fetch cannot be combined with --watch; use g or --interval"
            )
        if interval is not None:
            raise SystemExit("--fetch cannot be combined with --interval")
        _print_fetched_status(path, base=base, stale_days=stale_days)
        return

    print_branch_status(
        path,
        base=base,
        stale_days=stale_days,
        watch=watch,
        interval=interval,
    )


@app.command
def recent(
    path: Path = Path("."),
    /,
    *,
    limit: int = 20,
    watch: bool = False,
    interval: float | None = None,
    fetch: bool = False,
    base: str = "master",
) -> None:
    """Show a feed of the most recent commits across branches.

    Parameters
    ----------
    path:
        Repository path.
    limit:
        Maximum number of commits to display.
    watch:
        Continuously refresh until q or Ctrl-C. Use arrows to select, Enter to
        inspect a commit, and g to fetch/display remote commits.
    interval:
        In watch mode, automatically fetch remotes every this many seconds.
    fetch:
        Fetch remotes once before the first snapshot. In watch mode this seeds
        the initial remote snapshot without enabling periodic fetching.
    base:
        Base ref used by the per-commit detail view.
    """
    print_recent_commits(
        path,
        limit=limit,
        watch=watch,
        interval=interval,
        fetch_first=fetch,
        base=base,
    )


@app.command
def watch(
    remote: str,
    path: Path = Path("."),
    /,
    *,
    limit: int = 50,
    interval: float | None = None,
    base: str = "master",
    follow: bool = False,
) -> None:
    """Watch a reverse-chronological commit feed from one remote.

    Parameters
    ----------
    remote:
        Git remote to watch, for example origin.
    path:
        Repository path.
    limit:
        Maximum number of commits to display.
    interval:
        Fetch this remote automatically every this many seconds.
    base:
        Base ref used by the per-commit detail view.
    follow:
        Start with the cursor pinned to the newest commit. Press f to toggle.
    """
    watch_remote(
        remote,
        path,
        limit=limit,
        interval=interval,
        base=base,
        follow=follow,
    )


@app.command
def show(
    revision: str,
    path: Path = Path("."),
    /,
    *,
    base: str = "master",
) -> None:
    """Show detailed information for one commit.

    Parameters
    ----------
    revision:
        Commit SHA or other commit-ish.
    path:
        Repository path.
    base:
        Base ref used to report whether the commit has been incorporated.
    """
    print_commit_detail(path, revision, base=base)
