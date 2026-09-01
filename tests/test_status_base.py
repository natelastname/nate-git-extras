import subprocess
from pathlib import Path

import nate_git_extras.status as status


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def init_repo(path: Path) -> None:
    git(path, "init", "-b", "master")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    git(path, "commit", "--allow-empty", "-m", "initial")


def test_status_shows_base_first_and_diverged_remote_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

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


def test_status_merges_ready_branches_and_blocks_dirty_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

    git(repo, "switch", "-c", "fast")
    git(repo, "commit", "--allow-empty", "-m", "fast work")
    git(repo, "switch", "master")

    _, branches = status.collect_branch_status(repo)
    base = branches[0]
    fast = next(branch for branch in branches if branch.name == "fast")
    assert fast.merge == "ff"
    ok, message = status._merge_branch(base, fast)
    assert ok
    assert message == "merged fast → master"
    assert git(repo, "rev-parse", "master") == git(repo, "rev-parse", "fast")

    git(repo, "switch", "-c", "clean")
    (repo / "clean.txt").write_text("clean\n", encoding="utf-8")
    git(repo, "add", "clean.txt")
    git(repo, "commit", "-m", "clean work")
    git(repo, "switch", "master")
    (repo / "master.txt").write_text("master\n", encoding="utf-8")
    git(repo, "add", "master.txt")
    git(repo, "commit", "-m", "master work")

    _, branches = status.collect_branch_status(repo)
    base = branches[0]
    clean = next(branch for branch in branches if branch.name == "clean")
    assert clean.merge == "clean"
    ok, message = status._merge_branch(base, clean)
    assert ok
    assert message == "merged clean → master"
    assert len(git(repo, "rev-list", "--parents", "-n", "1", "master").split()) == 3

    git(repo, "switch", "-c", "blocked")
    git(repo, "commit", "--allow-empty", "-m", "blocked work")
    git(repo, "switch", "master")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    _, branches = status.collect_branch_status(repo)
    base = branches[0]
    blocked = next(branch for branch in branches if branch.name == "blocked")
    ok, message = status._merge_branch(base, blocked)
    assert not ok
    assert message == "merge blocked: base worktree is dirty"
