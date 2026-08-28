"""Human-oriented local and remote branch listing."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.text import Text

from .git_utils import find_git_root


@dataclass(frozen=True, slots=True)
class LocalBranch:
    name: str
    current: bool
    upstream: str | None


@dataclass(frozen=True, slots=True)
class RemoteBranch:
    name: str
    local_counterpart: str | None
    tracked_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RemoteDefault:
    name: str
    target: str


@dataclass(frozen=True, slots=True)
class BranchListing:
    local: tuple[LocalBranch, ...]
    remote: tuple[RemoteBranch, ...]
    remote_defaults: tuple[RemoteDefault, ...]


def _git_output(git_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(git_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _short_ref(refname: str) -> str:
    for prefix in ("refs/heads/", "refs/remotes/"):
        if refname.startswith(prefix):
            return refname[len(prefix) :]
    return refname


def _remote_branch_name(short_name: str, remote_names: tuple[str, ...]) -> str | None:
    ordered_remotes = sorted(remote_names, key=len, reverse=True)
    for remote_name in ordered_remotes:
        prefix = f"{remote_name}/"
        if short_name.startswith(prefix):
            return short_name[len(prefix) :]
    return None


def collect_branches(path: Path, *, fetch: bool = False) -> BranchListing:
    git_root = find_git_root(path)
    if git_root is None:
        raise RuntimeError(f"not inside a Git repository: {path}")

    if fetch:
        subprocess.run(
            ["git", "-C", str(git_root), "fetch", "--all", "--prune"],
            check=True,
        )

    local: list[LocalBranch] = []
    local_output = _git_output(
        git_root,
        "for-each-ref",
        "--format=%(refname)\t%(HEAD)\t%(upstream)",
        "refs/heads",
    )
    for line in local_output.splitlines():
        refname, head, upstream_ref = line.split("\t", maxsplit=2)
        name = _short_ref(refname)
        upstream = _short_ref(upstream_ref) if upstream_ref else ""
        local.append(
            LocalBranch(
                name=name,
                current=head == "*",
                upstream=upstream or None,
            )
        )

    local.sort(key=lambda branch: (not branch.current, branch.name.casefold()))

    tracked_by: dict[str, list[str]] = {}
    for branch in local:
        if branch.upstream is None:
            continue
        tracked_by.setdefault(branch.upstream, []).append(branch.name)

    remote_names = tuple(
        line for line in _git_output(git_root, "remote").splitlines() if line
    )
    local_names = {branch.name for branch in local}

    remote: list[RemoteBranch] = []
    remote_defaults: list[RemoteDefault] = []
    remote_output = _git_output(
        git_root,
        "for-each-ref",
        "--format=%(refname)\t%(symref)",
        "refs/remotes",
    )
    for line in remote_output.splitlines():
        refname, symref = line.split("\t", maxsplit=1)
        name = _short_ref(refname)
        if symref:
            remote_defaults.append(
                RemoteDefault(name=name, target=_short_ref(symref))
            )
            continue

        branch_name = _remote_branch_name(name, remote_names)
        local_counterpart = None
        if branch_name in local_names:
            local_counterpart = branch_name

        trackers = tracked_by.get(name, [])
        trackers.sort(key=str.casefold)
        remote.append(
            RemoteBranch(
                name=name,
                local_counterpart=local_counterpart,
                tracked_by=tuple(trackers),
            )
        )

    remote.sort(key=lambda branch: branch.name.casefold())
    remote_defaults.sort(key=lambda default: default.name.casefold())
    return BranchListing(
        local=tuple(local),
        remote=tuple(remote),
        remote_defaults=tuple(remote_defaults),
    )


def _remote_status(branch: RemoteBranch) -> str:
    if branch.tracked_by:
        names = ", ".join(branch.tracked_by)
        return f"[tracked by {names}]"
    if branch.local_counterpart is not None:
        return f"[local {branch.local_counterpart}; not tracking]"
    return "[remote only]"


def format_branches(listing: BranchListing) -> str:
    lines = ["Local branches"]
    if listing.local:
        for branch in listing.local:
            marker = "*" if branch.current else " "
            upstream = ""
            if branch.upstream is not None:
                upstream = f" -> {branch.upstream}"
            lines.append(f"  {marker} {branch.name}{upstream}")
    else:
        lines.append("  (none)")

    lines.extend(["", "Remote branches"])
    if listing.remote:
        width = max(len(branch.name) for branch in listing.remote)
        for branch in listing.remote:
            lines.append(f"    {branch.name:<{width}}  {_remote_status(branch)}")
    else:
        lines.append("  (none)")

    lines.extend(["", "Remote defaults"])
    if listing.remote_defaults:
        for default in listing.remote_defaults:
            lines.append(f"    {default.name} -> {default.target}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def render_branches(listing: BranchListing) -> Text:
    output = Text()
    first_line = True

    def add_line(*parts: tuple[str, str | None]) -> None:
        nonlocal first_line
        if not first_line:
            output.append("\n")
        first_line = False
        for value, style in parts:
            output.append(value, style=style)

    add_line(("Local branches", "bold"))
    if listing.local:
        for branch in listing.local:
            branch_style = "bold green" if branch.current else "cyan"
            marker = "*" if branch.current else " "
            parts: list[tuple[str, str | None]] = [
                (f"  {marker} ", branch_style),
                (branch.name, branch_style),
            ]
            if branch.upstream is not None:
                parts.extend(
                    [
                        (" -> ", "dim"),
                        (branch.upstream, "magenta"),
                    ]
                )
            add_line(*parts)
    else:
        add_line(("  (none)", "dim"))

    add_line(("", None))
    add_line(("Remote branches", "bold"))
    if listing.remote:
        width = max(len(branch.name) for branch in listing.remote)
        for branch in listing.remote:
            status_style = "green" if branch.tracked_by else "yellow"
            add_line(
                ("    ", None),
                (f"{branch.name:<{width}}", "magenta"),
                ("  ", None),
                (_remote_status(branch), status_style),
            )
    else:
        add_line(("  (none)", "dim"))

    add_line(("", None))
    add_line(("Remote defaults", "bold"))
    if listing.remote_defaults:
        for default in listing.remote_defaults:
            add_line(
                ("    ", None),
                (default.name, "magenta"),
                (" -> ", "dim"),
                (default.target, "magenta"),
            )
    else:
        add_line(("  (none)", "dim"))

    return output


def _should_page(console: Console, output: Text) -> bool:
    if not console.is_terminal:
        return False
    rendered_lines = console.render_lines(output, console.options, pad=False)
    return len(rendered_lines) > max(console.height - 1, 1)


def print_branches(
    path: Path,
    *,
    fetch: bool = False,
    pager: bool = True,
    color: bool = True,
) -> None:
    listing = collect_branches(path, fetch=fetch)
    output = render_branches(listing)
    console = Console() if color else Console(color_system=None)

    if pager and _should_page(console, output):
        with console.pager(styles=color):
            console.print(output)
        return

    console.print(output)
