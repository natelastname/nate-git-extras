import io
from pathlib import Path
import subprocess

from rich.console import Console

from nate_git_extras.branches import (
    BranchListing,
    LocalBranch,
    _should_page,
    collect_branches,
    format_branches,
    render_branches,
)


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def init_git_repo(path: Path) -> None:
    run_git(path, "init")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test User")
    run_git(path, "commit", "--allow-empty", "-m", "initial")


def test_branch_listing_groups_local_remote_and_default_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    run_git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    run_git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    run_git(repo, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
    run_git(
        repo,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/master",
    )
    run_git(repo, "branch", "--set-upstream-to=origin/master", "master")
    run_git(repo, "branch", "feature/local")

    output = format_branches(collect_branches(repo))

    assert output == """Local branches
  * master -> origin/master
    feature/local

Remote branches
    origin/master       [tracked by master]
    origin/remote-only  [remote only]

Remote defaults
    origin/HEAD -> origin/master"""


def test_branch_listing_distinguishes_untracked_same_name_local_branch(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    run_git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    run_git(repo, "update-ref", "refs/remotes/origin/topic", "HEAD")
    run_git(repo, "branch", "topic")

    output = format_branches(collect_branches(repo))

    assert "origin/topic  [local topic; not tracking]" in output


def test_colored_render_preserves_plain_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    run_git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    run_git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    run_git(repo, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
    run_git(repo, "branch", "--set-upstream-to=origin/master", "master")

    listing = collect_branches(repo)
    rendered = render_branches(listing)
    styles = {str(span.style) for span in rendered.spans}

    assert rendered.plain == format_branches(listing)
    assert "bold green" in styles
    assert "magenta" in styles
    assert "yellow" in styles


def test_branch_listing_pages_only_when_output_exceeds_terminal_height() -> None:
    listing = BranchListing(
        local=tuple(
            LocalBranch(name=f"feature/{index}", current=index == 0, upstream=None)
            for index in range(10)
        ),
        remote=(),
        remote_defaults=(),
    )
    output = render_branches(listing)

    tall_terminal = Console(
        file=io.StringIO(),
        force_terminal=True,
        width=80,
        height=40,
    )
    short_terminal = Console(
        file=io.StringIO(),
        force_terminal=True,
        width=80,
        height=6,
    )
    pipe = Console(
        file=io.StringIO(),
        force_terminal=False,
        width=80,
        height=6,
    )

    assert not _should_page(tall_terminal, output)
    assert _should_page(short_terminal, output)
    assert not _should_page(pipe, output)
