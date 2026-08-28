"""Command-line interface for nate-git-extras."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from loguru import logger

from .branches import print_branches
from .cp import git_cp_many, git_cp_template
from .ls import print_tree

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
def branches(
    path: Path = Path("."),
    /,
    *,
    fetch: bool = False,
) -> None:
    """List local and remote branches without exposing Git ref internals.

    Parameters
    ----------
    path:
        Path inside the repository to inspect.
    fetch:
        Fetch and prune all remotes before listing branches.
    """
    print_branches(path, fetch=fetch)
