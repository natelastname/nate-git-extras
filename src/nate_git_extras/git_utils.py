from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def find_git_root(path: Path) -> Path | None:
    """Return the git repository root for *path*.

    If *path* is not inside a git working tree, returns None.
    """

    resolved = path.expanduser().resolve()
    start_dir = resolved
    if not start_dir.is_dir():
        start_dir = start_dir.parent

    result = subprocess.run(
        ["git", "-C", str(start_dir), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    return Path(result.stdout.strip()).resolve()


@dataclass(frozen=True, slots=True)
class GitIgnore:
    """Thin wrapper around git check-ignore for exact ignore semantics.

    In practice you want ignored_among() for directory traversals: it batches many
    paths into a single git invocation, which is dramatically faster than calling
    git for every file.
    """

    git_root: Path

    def _to_pathspec(self, path: Path) -> str:
        resolved = path.expanduser().resolve()
        if resolved.is_relative_to(self.git_root):
            rel = resolved.relative_to(self.git_root)
            pathspec = rel.as_posix()
        else:
            pathspec = str(resolved)

        # Directory-only ignore rules (like 'foo/') won't match without a trailing slash.
        if resolved.is_dir() and not pathspec.endswith("/"):
            pathspec = f"{pathspec}/"

        return pathspec

    def ignored_among(self, paths: list[Path]) -> set[Path]:
        if not paths:
            return set()

        spec_to_path: dict[str, Path] = {}
        specs: list[str] = []
        for p in paths:
            spec = self._to_pathspec(p)
            specs.append(spec)
            spec_to_path[spec] = p

        # -z + --stdin avoids quoting issues and makes output parsing trivial.
        stdin = "\0".join(specs) + "\0"
        result = subprocess.run(
            ["git", "-C", str(self.git_root), "check-ignore", "-z", "--stdin", "--"],
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
        )

        if result.returncode == 1:
            return set()
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git check-ignore failed")

        ignored: set[Path] = set()
        for spec in result.stdout.split("\0"):
            if not spec:
                continue
            ignored.add(spec_to_path[spec])

        return ignored


def is_git_ignored(path: Path) -> bool | None:
    """Return whether *path* is ignored by git.

    - If *path* is not in a git repo: return None
    - If *path* is in a git repo and ignored: return True
    - If *path* is in a git repo and not ignored: return False
    """

    resolved = path.expanduser().resolve()
    git_root = find_git_root(resolved)
    if git_root is None:
        return None

    checker = GitIgnore(git_root)
    ignored = checker.ignored_among([resolved])
    return resolved in ignored
