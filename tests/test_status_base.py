from pathlib import Path
import subprocess

import nate_git_extras.status as status


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_status_shows_base_first_and_diverged_remote_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "master")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "commit", "--allow-empty", "-m", "initial")

    git(repo, "switch", "-c", "remote-master")
    git(repo, "commit", "--allow-empty", "-m", "remote change")
    remote_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "master")
    git(repo, "update-ref", "refs/remotes/origin/master", remote_sha)
    git(repo, "branch", "-D", "remote-master")

    _, local = status.collect_branch_status(repo)
    assert local[0].name == "master"
    assert local[0].status == "BASE"
    assert (local[0].ahead, local[0].behind) == (0, 0)

    _, with_remotes = status.collect_branch_status(repo, include_remotes=True)
    assert with_remotes[0].name == "master"
    remote = next(branch for branch in with_remotes if branch.name == "origin/master")
    assert remote.remote
    assert remote.status == "READY"
    assert (remote.ahead, remote.behind) == (1, 0)

    git(repo, "update-ref", "refs/remotes/origin/master", "master")
    _, synced = status.collect_branch_status(repo, include_remotes=True)
    assert "origin/master" not in {branch.name for branch in synced}
