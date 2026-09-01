"""Per-commit detail and file diff views."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.layout import Layout
from rich.table import Table
from rich.text import Text

from .git_utils import find_git_root
from .status import _age, _fit


@dataclass(frozen=True, slots=True)
class CommitFile:
    path: str
    additions: int | None
    deletions: int | None


@dataclass(frozen=True, slots=True)
class CommitDetail:
    sha: str
    timestamp: int
    author_name: str
    author_email: str
    subject: str
    body: str
    parents: tuple[str, ...]
    refs: tuple[str, ...]
    branch: str
    remote: bool
    files: tuple[CommitFile, ...]
    additions: int
    deletions: int
    binary_files: int
    base: str
    contained_in_base: bool | None


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


def _resolve_commit(root: Path, revision: str) -> str:
    result = _git(
        root,
        "rev-parse",
        "--verify",
        "--quiet",
        "--end-of-options",
        f"{revision}^{{commit}}",
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"commit does not exist: {revision}")
    return result.stdout.strip()


def branch_for_commit(
    root: Path, sha: str, *, include_remotes: bool
) -> tuple[str, bool]:
    for namespace, remote in (("refs/heads/", False), ("refs/remotes/", True)):
        if remote and not include_remotes:
            break
        exact = _git(
            root,
            "for-each-ref",
            f"--points-at={sha}",
            "--format=%(refname:short)\t%(symref)",
            namespace,
        )
        for line in exact.stdout.splitlines():
            if not line:
                continue
            name, symref = line.split("\t", 1)
            if not symref:
                return name, remote

    for pattern, remote in (("refs/heads/*", False), ("refs/remotes/*", True)):
        if remote and not include_remotes:
            break
        result = _git(
            root,
            "name-rev",
            "--name-only",
            "--no-undefined",
            f"--refs={pattern}",
            sha,
            check=False,
        )
        if result.returncode != 0:
            continue
        name = result.stdout.strip().split("~", 1)[0].split("^", 1)[0]
        if remote:
            name = name.removeprefix("remotes/")
        return name, remote
    return "—", False


def _containing_refs(root: Path, sha: str) -> tuple[str, ...]:
    result = _git(
        root,
        "for-each-ref",
        f"--contains={sha}",
        "--format=%(refname:short)\t%(symref)",
        "refs/heads/",
        "refs/remotes/",
    )
    refs: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        name, symref = line.split("\t", 1)
        if not symref:
            refs.append(name)
    refs.sort(key=lambda ref: (ref.startswith("origin/"), "/" in ref, ref))
    return tuple(refs)


def _metadata(root: Path, sha: str) -> tuple[int, str, str, tuple[str, ...], str, str]:
    result = _git(
        root,
        "show",
        "-s",
        "--format=%ct%x1f%an%x1f%ae%x1f%P%x1f%s%x1f%b",
        sha,
    )
    timestamp, author, email, parents, subject, body = result.stdout.split("\x1f", 5)
    return (
        int(timestamp),
        author,
        email,
        tuple(parents.split()) if parents.strip() else (),
        subject.strip(),
        body.strip(),
    )


def _numstat(root: Path, sha: str, parents: tuple[str, ...]) -> tuple[CommitFile, ...]:
    if parents:
        result = _git(root, "diff", "--no-renames", "--numstat", parents[0], sha, "--")
    else:
        result = _git(root, "show", "--format=", "--no-renames", "--numstat", sha, "--")

    files: list[CommitFile] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        added, deleted, path = line.split("\t", 2)
        files.append(
            CommitFile(
                path,
                None if added == "-" else int(added),
                None if deleted == "-" else int(deleted),
            )
        )
    return tuple(files)


def _contained_in(root: Path, sha: str, base: str) -> bool | None:
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
        return None
    return _git(root, "merge-base", "--is-ancestor", sha, base, check=False).returncode == 0


def collect_commit_detail(
    path: Path,
    revision: str,
    *,
    base: str = "master",
) -> tuple[Path, CommitDetail]:
    root = find_git_root(path)
    if root is None:
        raise SystemExit(f"not inside a Git repository: {path}")

    sha = _resolve_commit(root, revision)
    timestamp, author, email, parents, subject, body = _metadata(root, sha)
    branch, remote = branch_for_commit(root, sha, include_remotes=True)
    refs = _containing_refs(root, sha)
    files = _numstat(root, sha, parents)
    additions = sum(file.additions or 0 for file in files)
    deletions = sum(file.deletions or 0 for file in files)
    binary_files = sum(file.additions is None for file in files)

    return root, CommitDetail(
        sha=sha,
        timestamp=timestamp,
        author_name=author,
        author_email=email,
        subject=subject,
        body=body,
        parents=parents,
        refs=refs,
        branch=branch,
        remote=remote,
        files=files,
        additions=additions,
        deletions=deletions,
        binary_files=binary_files,
        base=base,
        contained_in_base=_contained_in(root, sha, base),
    )


def commit_patch(root: Path, detail: CommitDetail, file: CommitFile) -> str:
    if detail.parents:
        result = _git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--unified=3",
            detail.parents[0],
            detail.sha,
            "--",
            file.path,
        )
    else:
        result = _git(
            root,
            "show",
            "--format=",
            "--no-ext-diff",
            "--no-renames",
            "--unified=3",
            detail.sha,
            "--",
            file.path,
        )
    return result.stdout.rstrip()


def _base_text(detail: CommitDetail) -> str:
    if detail.contained_in_base is None:
        state = "unavailable"
    elif detail.contained_in_base:
        state = "contained"
    else:
        state = "not merged"
    return f"{detail.base} · {state}"


def _stats_text(file: CommitFile) -> str:
    if file.additions is None:
        return " binary "
    return f"+{file.additions} / -{file.deletions}"


def _detail_lines(
    detail: CommitDetail,
    *,
    max_body_lines: int | None = None,
) -> list[Text]:
    refs = ", ".join(detail.refs[:5]) if detail.refs else "—"
    if len(detail.refs) > 5:
        refs += f", +{len(detail.refs) - 5} more"
    parents = ", ".join(parent[:8] for parent in detail.parents) or "—"

    lines = [
        Text.assemble(("Commit  ", "bold"), (detail.sha[:12], "bold cyan")),
        Text.assemble(
            ("Branch  ", "bold"),
            (detail.branch, "cyan" if detail.remote else ""),
        ),
        Text.assemble(
            ("Age     ", "bold"),
            _age(int(time.time()) - detail.timestamp),
        ),
        Text.assemble(
            ("Author  ", "bold"),
            f"{detail.author_name} <{detail.author_email}>",
        ),
        Text.assemble(("Parents ", "bold"), parents),
        Text.assemble(("Refs    ", "bold"), refs),
        Text.assemble(("Base    ", "bold"), _base_text(detail)),
        Text(""),
        Text(detail.subject, style="bold"),
    ]
    if detail.body:
        body_lines = detail.body.splitlines()
        shown = body_lines if max_body_lines is None else body_lines[:max_body_lines]
        lines.extend(Text(line) for line in shown)
        if max_body_lines is not None and len(body_lines) > max_body_lines:
            lines.append(Text("…", style="dim"))
    return lines


def _file_row(file: CommitFile, width: int) -> Text:
    stats_width = 13
    row = Text()
    if file.additions is None:
        row.append(_fit("binary", stats_width), style="yellow")
    else:
        row.append(
            _fit(f"+{file.additions} -{file.deletions}", stats_width),
            style="green" if (file.additions or 0) >= (file.deletions or 0) else "",
        )
    row.append(" ")
    row.append(_fit(file.path, max(1, width - stats_width - 1)))
    return row


def commit_detail_layout(
    root: Path,
    detail: CommitDetail,
    *,
    selected_file: int,
    width: int,
    height: int,
) -> Layout:
    header = Text.assemble(
        ("Commit detail", "bold"),
        ("  ", "dim"),
        (str(root), "dim"),
        (
            "  ↑/↓ select file · Enter/d diff · Esc/q back",
            "dim",
        ),
    )
    info = _detail_lines(detail, max_body_lines=6)
    fixed = len(info) + 3
    limit = max(1, height - fixed - 1)
    start = (
        0
        if len(detail.files) <= limit
        else max(
            0,
            min(
                selected_file - limit // 2,
                len(detail.files) - limit,
            ),
        )
    )
    file_lines: list[Text] = [Text("Files", style="bold")]
    for index in range(start, min(start + limit, len(detail.files))):
        row = _file_row(detail.files[index], width)
        if index == selected_file:
            row.stylize("reverse")
        file_lines.append(row)
    if not detail.files:
        file_lines.append(Text("  no file changes", style="dim"))

    footer = Text(
        f"{len(detail.files)} files · +{detail.additions} / -{detail.deletions}",
        style="dim",
    )
    if detail.binary_files:
        footer.append(f" · {detail.binary_files} binary", style="yellow")
    footer.append(f" · Base {_base_text(detail)}", style="dim")

    layout = Layout()
    layout.split_column(
        Layout(Group(header, *info, *file_lines)),
        Layout(footer, size=1),
    )
    return layout


def _styled_patch_line(line: str, width: int) -> Text:
    style = ""
    if line.startswith("@@"):
        style = "cyan"
    elif line.startswith("+++") or line.startswith("---"):
        style = "bold"
    elif line.startswith("+"):
        style = "green"
    elif line.startswith("-"):
        style = "red"
    elif line.startswith(("diff --git", "index ")):
        style = "dim"
    return Text(_fit(line, width), style=style, no_wrap=True)


def commit_patch_layout(
    root: Path,
    detail: CommitDetail,
    file: CommitFile,
    patch: str,
    *,
    offset: int,
    width: int,
    height: int,
) -> Layout:
    header = Text.assemble(
        (detail.sha[:8], "bold cyan"),
        (" · ", "dim"),
        (file.path, "bold"),
        ("  ↑/↓ scroll · Esc/q back", "dim"),
    )
    lines = patch.splitlines() or ["(no textual diff)"]
    limit = max(1, height - 2)
    max_offset = max(0, len(lines) - limit)
    offset = max(0, min(offset, max_offset))
    visible = [_styled_patch_line(line, width) for line in lines[offset : offset + limit]]

    footer = Text(
        f"{_stats_text(file).strip()} · lines {offset + 1}-{min(len(lines), offset + limit)}"
        f" of {len(lines)}",
        style="dim",
    )
    layout = Layout()
    layout.split_column(
        Layout(Group(header, *visible)),
        Layout(footer, size=1),
    )
    return layout


def print_commit_detail(
    path: Path,
    revision: str,
    *,
    base: str = "master",
) -> None:
    root, detail = collect_commit_detail(path, revision, base=base)
    table = Table(box=None, pad_edge=False)
    table.add_column("Change", no_wrap=True)
    table.add_column("File", overflow="ellipsis", no_wrap=True)
    for file in detail.files:
        table.add_row(_stats_text(file), file.path)

    footer = Text(
        f"{len(detail.files)} files changed · +{detail.additions} / -{detail.deletions}"
        f" · Base {_base_text(detail)}",
        style="dim",
    )
    if detail.binary_files:
        footer.append(f" · {detail.binary_files} binary", style="yellow")

    Console().print(Group(*_detail_lines(detail), Text(""), table, footer))
