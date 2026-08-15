"""Git-aware copy that understands gitignore and multiple sources.

This is an intentionally opinionated "cp" meant for project code/templates:

- Source paths must be inside a Git work tree.
- Sources may be directories (copied recursively) or regular files.
- Symlinks are rejected.
- Globs are expanded by the shell; multiple sources require the
  destination to be an existing directory (cp-style).
- Uses Git ignore semantics (via "git check-ignore") to skip ignored paths.

Verbose output is done with loguru.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from .git_utils import GitIgnore, find_git_root


@dataclass(frozen=True)
class CopyPlan:
    git_root: Path
    src_dir: Path
    dst_dir: Path
    ignore: GitIgnore


def _is_real_dir(path: Path) -> bool:
    # Path.is_dir() follows symlinks; we explicitly reject symlinked dirs.
    return path.exists() and path.is_dir() and not path.is_symlink()


def _resolve_dst(src_dir: Path, dst: Path) -> Path:
    if dst.exists() and dst.is_symlink():
        raise SystemExit(f"destination must not be a symlink: {dst}")

    if dst.exists() and dst.is_file():
        raise SystemExit(f"destination is a file: {dst}")

    if dst.exists() and dst.is_dir():
        # Match mv-style behavior: copying a directory into an existing directory
        # creates dst/src_name.
        return (dst / src_dir.name).resolve()

    return dst.resolve()


def _configure_logging(*, enabled: bool) -> None:
    if not enabled:
        return

    # CLI-style output: keep it plain and deterministic.
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{message}")


def _log_copy(src: Path, dst: Path) -> None:
    logger.info("COPY  {} -> {}", src, dst)


def _log_skip(path: Path, reason: str) -> None:
    logger.info("SKIP  {} ({})", path, reason)


def _build_plan(src_dir: Path, dst_arg: Path) -> CopyPlan:
    if not _is_real_dir(src_dir):
        raise SystemExit(
            "source must be a directory and must not be a symlink: "
            f"{src_dir}"
        )

    src_dir = src_dir.expanduser().resolve()

    git_root = find_git_root(src_dir)
    if git_root is None:
        raise SystemExit(f"{src_dir} is not inside a git work tree")

    # Treat "git repo" as "a repo with at least one commit". This avoids silently
    # copying from accidental/uninitialized .git directories.
    has_commit = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if has_commit.returncode != 0:
        raise SystemExit(f"{src_dir} is not part of a git repo with commits")

    if not src_dir.is_relative_to(git_root):
        raise SystemExit(f"source directory {src_dir} is not inside repo root {git_root}")

    dst_dir = _resolve_dst(src_dir, dst_arg)

    if dst_dir == src_dir:
        raise SystemExit("destination resolves to the source directory")

    if dst_dir.is_relative_to(src_dir):
        raise SystemExit("destination cannot be inside the source directory")

    ignore = GitIgnore(git_root)

    return CopyPlan(git_root=git_root, src_dir=src_dir, dst_dir=dst_dir, ignore=ignore)


def git_cp(src_dir: Path, dst: Path, *, verbose: bool, dry_run: bool) -> None:
    plan = _build_plan(src_dir, dst)
    _execute_plan(plan, verbose=verbose, dry_run=dry_run)


def _execute_plan(plan: CopyPlan, *, verbose: bool, dry_run: bool) -> None:
    log_actions = verbose or dry_run

    if not dry_run:
        plan.dst_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0

    for root, dirs, files in os.walk(plan.src_dir, topdown=True, followlinks=False):
        root_path = Path(root)

        # Evaluate ignore status for immediate children in one git call.
        candidates: list[Path] = []

        dir_paths: list[Path] = []
        for d in dirs:
            dir_path = root_path / d
            dir_paths.append(dir_path)

            if d == ".git":
                continue
            if dir_path.is_symlink():
                continue
            candidates.append(dir_path)

        file_paths: list[Path] = []
        for f in files:
            file_path = root_path / f
            file_paths.append(file_path)

            if file_path.is_symlink():
                continue
            if not file_path.is_file():
                continue
            candidates.append(file_path)

        ignored = plan.ignore.ignored_among(candidates)

        kept_dirs: list[str] = []
        for d, dir_path in zip(dirs, dir_paths, strict=True):
            if d == ".git":
                if log_actions:
                    _log_skip(dir_path, ".git directory")
                skipped += 1
                continue

            if dir_path.is_symlink():
                if log_actions:
                    _log_skip(dir_path, "symlink directory")
                skipped += 1
                continue

            if dir_path in ignored:
                if log_actions:
                    _log_skip(dir_path, "git-ignored")
                skipped += 1
                continue

            kept_dirs.append(d)

        # Prune ignored dirs so os.walk doesn't traverse them.
        dirs[:] = kept_dirs

        for file_path in file_paths:
            if file_path.is_symlink():
                if log_actions:
                    _log_skip(file_path, "symlink")
                skipped += 1
                continue

            if not file_path.is_file():
                if log_actions:
                    _log_skip(file_path, "not a regular file")
                skipped += 1
                continue

            if file_path in ignored:
                if log_actions:
                    _log_skip(file_path, "git-ignored")
                skipped += 1
                continue

            rel_under_src = file_path.relative_to(plan.src_dir)
            dst_path = plan.dst_dir / rel_under_src

            if log_actions:
                _log_copy(file_path, dst_path)

            if not dry_run:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dst_path)

            copied += 1

    if log_actions:
        logger.info("DONE  copied={} skipped={} dst={}", copied, skipped, plan.dst_dir)





def _build_template_plan(src_dir: Path, dst_root_arg: Path) -> CopyPlan:
    if not _is_real_dir(src_dir):
        raise SystemExit(
            "template source must be a directory and must not be a symlink: "
            f"{src_dir}"
        )

    src_dir = src_dir.expanduser().resolve()

    git_root = find_git_root(src_dir)
    if git_root is None:
        raise SystemExit(f"{src_dir} is not inside a git work tree")

    has_commit = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if has_commit.returncode != 0:
        raise SystemExit(f"{src_dir} is not part of a git repo with commits")

    if not src_dir.is_relative_to(git_root):
        raise SystemExit(
            f"template source directory {src_dir} is not inside repo root {git_root}"
        )

    dst_root = dst_root_arg.expanduser().resolve()

    if dst_root.exists() and dst_root.is_symlink():
        raise SystemExit(f"template destination must not be a symlink: {dst_root}")

    if dst_root.exists() and not dst_root.is_dir():
        raise SystemExit(f"template destination must be a directory: {dst_root}")

    if dst_root == src_dir:
        raise SystemExit("template destination resolves to the source directory")

    if dst_root.is_relative_to(src_dir):
        raise SystemExit("template destination cannot be inside the source directory")

    ignore = GitIgnore(git_root)

    return CopyPlan(git_root=git_root, src_dir=src_dir, dst_dir=dst_root, ignore=ignore)



def git_cp_template(src_dir: Path, dst_root: Path, *, verbose: bool, dry_run: bool) -> None:
    plan = _build_template_plan(src_dir, dst_root)
    _execute_plan(plan, verbose=verbose, dry_run=dry_run)

def _git_cp_file(src_file: Path, dst: Path, *, verbose: bool, dry_run: bool) -> None:
    """Copy a single regular file, respecting git ignore semantics."""
    src_file = src_file.expanduser().resolve()

    git_root = find_git_root(src_file)
    if git_root is None:
        raise SystemExit(f"{src_file} is not inside a git work tree")

    has_commit = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if has_commit.returncode != 0:
        raise SystemExit(f"{src_file} is not part of a git repo with commits")

    if not src_file.is_relative_to(git_root):
        raise SystemExit(f"source file {src_file} is not inside repo root {git_root}")

    dst_path = _resolve_dst(src_file, dst)
    if dst_path == src_file:
        raise SystemExit("destination resolves to the source file")

    ignore = GitIgnore(git_root)
    ignored = ignore.ignored_among([src_file])

    log_actions = verbose or dry_run
    copied = 0
    skipped = 0

    if src_file in ignored:
        if log_actions:
            _log_skip(src_file, "git-ignored")
        skipped += 1
    else:
        if log_actions:
            _log_copy(src_file, dst_path)
        if not dry_run:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_path)
        copied += 1

    if log_actions:
        logger.info("DONE  copied={} skipped={} dst={}", copied, skipped, dst_path)



def _git_cp_one(src: Path, dst: Path, *, verbose: bool, dry_run: bool) -> None:
    """Dispatch copying for a single source path (file or directory)."""
    src = src.expanduser().resolve()

    if not src.exists():
        raise SystemExit(f"source must exist: {src}")

    if src.is_symlink():
        raise SystemExit(f"source must not be a symlink: {src}")

    if src.is_dir():
        git_cp(src, dst, verbose=verbose, dry_run=dry_run)
        return

    if src.is_file():
        _git_cp_file(src, dst, verbose=verbose, dry_run=dry_run)
        return

    raise SystemExit(f"source is not a regular file or directory: {src}")



def git_cp_many(src_paths: list[Path], dst: Path, *, verbose: bool, dry_run: bool) -> None:
    """Copy one or more sources into *dst*.

    When more than one source is provided, *dst* must be an existing directory
    (cp-style semantics). Globs are expanded by the shell before reaching
    this function.
    """

    if not src_paths:
        raise SystemExit("at least one source path is required")

    if len(src_paths) > 1:
        dst_resolved = dst.expanduser().resolve()
        if not dst_resolved.exists() or not dst_resolved.is_dir():
            raise SystemExit(
                "when copying multiple sources, destination must be an existing "
                f"directory: {dst_resolved}"
            )

    for src in src_paths:
        _git_cp_one(src, dst, verbose=verbose, dry_run=dry_run)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nate_git_cp")
    parser.add_argument(
        "src",
        type=Path,
        nargs="+",
        help=(
            "Source path(s). Globs are expanded by the shell, and multiple "
            "sources require the destination to be an existing directory."
        ),
    )
    parser.add_argument(
        "dst",
        type=Path,
        help=(
            "Destination. If it is an existing directory, copy into it as dst/src_name. "
            "Otherwise, copy to dst."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log every file copied/skipped",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Do not copy; only log what would happen (same output as --verbose)",
    )
    parser.add_argument(
        "--template",
        action="store_true",
        help=(
            "Template copy mode: expect a single source directory and copy its "
            "contents into the destination directory (including dotfiles, "
            "subject to gitignore)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    _configure_logging(enabled=args.verbose or args.dry_run)

    if args.template:
        if len(args.src) != 1:
            raise SystemExit("template mode expects exactly one source path")
        git_cp_template(args.src[0], args.dst, verbose=args.verbose, dry_run=args.dry_run)
    else:
        git_cp_many(args.src, args.dst, verbose=args.verbose, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
