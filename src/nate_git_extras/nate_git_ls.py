import argparse
from pathlib import Path

from loguru import logger

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

    def sort_key(p: Path) -> tuple[int, str]:
        return (0 if p.is_dir() else 1, p.name.casefold())

    children.sort(key=sort_key)
    return children


def print_tree(
    root: Path, *, include_ignored: bool = False, traverse_ignored: bool = False
) -> None:
    root = root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)

    if root.is_file():
        print(root.name)
        return

    gitignore: GitIgnore | None = None
    if not include_ignored or not traverse_ignored:
        git_root = find_git_root(root)
        if git_root is not None:
            gitignore = GitIgnore(git_root)

    print(root.name)

    def walk(dir_path: Path, prefix: str) -> None:
        children = _iter_children(dir_path)

        ignored = set()
        if gitignore is not None:
            ignored = gitignore.ignored_among(children)
            children = [
                child for child in children if child not in ignored or include_ignored
            ]

        for index in range(len(children)):
            child = children[index]
            is_last = index == len(children) - 1

            branch = ELBOW if is_last else TEE
            print(f"{prefix}{branch} {child.name}")

            if child.is_dir():
                # Only traverse non-ignored directories, even if printed.
                if child in ignored and not traverse_ignored:
                    continue

                next_prefix = prefix + (SPACE_INDENT if is_last else PIPE_INDENT)
                walk(child, next_prefix)

    walk(root, "")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nate_git_ls")
    parser.add_argument(
        "path",
        nargs="?",
        default=Path("."),
        type=Path,
        help="Directory to print as a tree (default: current directory)",
    )
    parser.add_argument(
        "--include-ignored",
        dest="include_ignored",
        action="store_true",
        help="Include files/directories ignored by git (default excludes them)",
    )
    parser.add_argument(
        "--no-print-ignore-dirs",
        dest="traverse_ignored",
        action="store_false",
        help="Do not print gitignored directories (default: print, do not traverse)",
    )

    args = parser.parse_args(argv)
    logger.debug(
        "tree path={} include_ignored={} traverse_ignored={} ",
        args.path,
        args.include_ignored,
        args.traverse_ignored,
    )
    print_tree(
        args.path,
        include_ignored=args.include_ignored,
        traverse_ignored=args.traverse_ignored,
    )


if __name__ == "__main__":
    main()
