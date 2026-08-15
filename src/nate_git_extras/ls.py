"""Tree-style repository listing."""

from pathlib import Path

from .git_utils import GitIgnore, find_git_root

TEE = "├──"
ELBOW = "└──"
PIPE_INDENT = "│   "
SPACE_INDENT = "    "


def _iter_children(dir_path: Path) -> list[Path]:
    children: list[Path] = []
    for child in dir_path.iterdir():
        if child.name == ".git":
            continue
        children.append(child)

    def sort_key(path: Path) -> tuple[int, str]:
        return (0 if path.is_dir() else 1, path.name.casefold())

    children.sort(key=sort_key)
    return children


def print_tree(
    root: Path,
    *,
    include_ignored: bool = False,
    traverse_ignored: bool = False,
) -> None:
    root = root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if root.is_file():
        print(root.name)
        return

    if traverse_ignored:
        include_ignored = True

    gitignore: GitIgnore | None = None
    if not (include_ignored and traverse_ignored):
        git_root = find_git_root(root)
        if git_root is not None:
            gitignore = GitIgnore(git_root)

    print(root.name)

    def walk(dir_path: Path, prefix: str) -> None:
        children = _iter_children(dir_path)
        ignored: set[Path] = set()

        if gitignore is not None:
            ignored = gitignore.ignored_among(children)
            if not include_ignored:
                children = [child for child in children if child not in ignored]

        for index, child in enumerate(children):
            is_last = index == len(children) - 1
            branch = ELBOW if is_last else TEE
            print(f"{prefix}{branch} {child.name}")

            if not child.is_dir():
                continue
            if child in ignored and not traverse_ignored:
                continue

            next_prefix = prefix + (SPACE_INDENT if is_last else PIPE_INDENT)
            walk(child, next_prefix)

    walk(root, "")
