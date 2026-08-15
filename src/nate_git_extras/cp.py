"""Git-aware copy operations."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from .git_utils import GitIgnore, find_git_root


@dataclass(frozen=True, slots=True)
class CopyPlan:
    git_root: Path
    src_dir: Path
    dst_dir: Path
    ignore: GitIgnore


def _is_real_dir(path: Path) -> bool:
    return path.exists() and path.is_dir() and not path.is_symlink()


def _require_committed_git_root(path: Path) -> Path:
    git_root = find_git_root(path)
    if git_root is None:
        raise SystemExit(f"{path} is not inside a git work tree")

    has_commit = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if has_commit.returncode != 0:
        raise SystemExit(f"{path} is not part of a git repo with commits")

    if not path.is_relative_to(git_root):
        raise SystemExit(f"source {path} is not inside repo root {git_root}")

    return git_root


def _resolve_dst(src: Path, dst: Path) -> Path:
    dst = dst.expanduser()
    if dst.exists() and dst.is_symlink():
        raise SystemExit(f"destination must not be a symlink: {dst}")
    if dst.exists() and dst.is_file():
        raise SystemExit(f"destination is a file: {dst}")
    if dst.exists() and dst.is_dir():
        return (dst / src.name).resolve()
    return dst.resolve()


def _log_copy(src: Path, dst: Path) -> None:
    logger.info("COPY  {} -> {}", src, dst)


def _log_skip(path: Path, reason: str) -> None:
    logger.info("SKIP  {} ({})", path, reason)


def _build_plan(src_dir: Path, dst: Path) -> CopyPlan:
    src_dir = src_dir.expanduser()
    if not _is_real_dir(src_dir):
        raise SystemExit(
            f"source must be a directory and must not be a symlink: {src_dir}"
        )

    src_dir = src_dir.resolve()
    git_root = _require_committed_git_root(src_dir)
    dst_dir = _resolve_dst(src_dir, dst)

    if dst_dir == src_dir:
        raise SystemExit("destination resolves to the source directory")
    if dst_dir.is_relative_to(src_dir):
        raise SystemExit("destination cannot be inside the source directory")

    return CopyPlan(
        git_root=git_root,
        src_dir=src_dir,
        dst_dir=dst_dir,
        ignore=GitIgnore(git_root),
    )


def _build_template_plan(src_dir: Path, dst_root: Path) -> CopyPlan:
    src_dir = src_dir.expanduser()
    if not _is_real_dir(src_dir):
        raise SystemExit(
            "template source must be a directory and must not be a symlink: "
            f"{src_dir}"
        )

    src_dir = src_dir.resolve()
    git_root = _require_committed_git_root(src_dir)
    dst_root = dst_root.expanduser().resolve()

    if dst_root.exists() and dst_root.is_symlink():
        raise SystemExit(f"template destination must not be a symlink: {dst_root}")
    if dst_root.exists() and not dst_root.is_dir():
        raise SystemExit(f"template destination must be a directory: {dst_root}")
    if dst_root == src_dir:
        raise SystemExit("template destination resolves to the source directory")
    if dst_root.is_relative_to(src_dir):
        raise SystemExit("template destination cannot be inside the source directory")

    return CopyPlan(
        git_root=git_root,
        src_dir=src_dir,
        dst_dir=dst_root,
        ignore=GitIgnore(git_root),
    )


def _execute_plan(plan: CopyPlan, *, verbose: bool, dry_run: bool) -> None:
    log_actions = verbose or dry_run
    if not dry_run:
        plan.dst_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0

    for root, dirs, files in os.walk(plan.src_dir, topdown=True, followlinks=False):
        root_path = Path(root)
        candidates: list[Path] = []
        dir_paths: list[Path] = []

        for name in dirs:
            dir_path = root_path / name
            dir_paths.append(dir_path)
            if name == ".git" or dir_path.is_symlink():
                continue
            candidates.append(dir_path)

        file_paths: list[Path] = []
        for name in files:
            file_path = root_path / name
            file_paths.append(file_path)
            if file_path.is_symlink() or not file_path.is_file():
                continue
            candidates.append(file_path)

        ignored = plan.ignore.ignored_among(candidates)
        kept_dirs: list[str] = []

        for name, dir_path in zip(dirs, dir_paths, strict=True):
            reason: str | None = None
            if name == ".git":
                reason = ".git directory"
            elif dir_path.is_symlink():
                reason = "symlink directory"
            elif dir_path in ignored:
                reason = "git-ignored"

            if reason is not None:
                if log_actions:
                    _log_skip(dir_path, reason)
                skipped += 1
                continue

            kept_dirs.append(name)

        dirs[:] = kept_dirs

        for file_path in file_paths:
            reason = None
            if file_path.is_symlink():
                reason = "symlink"
            elif not file_path.is_file():
                reason = "not a regular file"
            elif file_path in ignored:
                reason = "git-ignored"

            if reason is not None:
                if log_actions:
                    _log_skip(file_path, reason)
                skipped += 1
                continue

            dst_path = plan.dst_dir / file_path.relative_to(plan.src_dir)
            if log_actions:
                _log_copy(file_path, dst_path)
            if not dry_run:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dst_path)
            copied += 1

    if log_actions:
        logger.info("DONE  copied={} skipped={} dst={}", copied, skipped, plan.dst_dir)


def git_cp(src_dir: Path, dst: Path, *, verbose: bool, dry_run: bool) -> None:
    _execute_plan(_build_plan(src_dir, dst), verbose=verbose, dry_run=dry_run)


def git_cp_template(
    src_dir: Path, dst_root: Path, *, verbose: bool, dry_run: bool
) -> None:
    _execute_plan(
        _build_template_plan(src_dir, dst_root),
        verbose=verbose,
        dry_run=dry_run,
    )


def _git_cp_file(src_file: Path, dst: Path, *, verbose: bool, dry_run: bool) -> None:
    src_file = src_file.expanduser()
    if not src_file.exists():
        raise SystemExit(f"source must exist: {src_file}")
    if src_file.is_symlink():
        raise SystemExit(f"source must not be a symlink: {src_file}")
    if not src_file.is_file():
        raise SystemExit(f"source is not a regular file: {src_file}")

    src_file = src_file.resolve()
    git_root = _require_committed_git_root(src_file)
    dst_path = _resolve_dst(src_file, dst)
    if dst_path == src_file:
        raise SystemExit("destination resolves to the source file")

    ignored = GitIgnore(git_root).ignored_among([src_file])
    log_actions = verbose or dry_run

    if src_file in ignored:
        if log_actions:
            _log_skip(src_file, "git-ignored")
        if log_actions:
            logger.info("DONE  copied=0 skipped=1 dst={}", dst_path)
        return

    if log_actions:
        _log_copy(src_file, dst_path)
    if not dry_run:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_path)
    if log_actions:
        logger.info("DONE  copied=1 skipped=0 dst={}", dst_path)


def _git_cp_one(src: Path, dst: Path, *, verbose: bool, dry_run: bool) -> None:
    src = src.expanduser()
    if not src.exists():
        raise SystemExit(f"source must exist: {src}")
    if src.is_symlink():
        raise SystemExit(f"source must not be a symlink: {src}")

    src = src.resolve()
    if src.is_dir():
        git_cp(src, dst, verbose=verbose, dry_run=dry_run)
        return
    if src.is_file():
        _git_cp_file(src, dst, verbose=verbose, dry_run=dry_run)
        return
    raise SystemExit(f"source is not a regular file or directory: {src}")


def git_cp_many(
    src_paths: list[Path], dst: Path, *, verbose: bool, dry_run: bool
) -> None:
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
