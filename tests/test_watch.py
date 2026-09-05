from pathlib import Path
import os
import subprocess
import time

from nate_git_extras.status import _poll_fetch
from nate_git_extras.watch import FetchStatus, _start_fetch, collect_remote_commits


def run_git(path: Path, *args: str, env: dict[str, str] | None = None) -> str:
    process_env = os.environ.copy()
    process_env.update(env or {})
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env=process_env,
    ).stdout.strip()


def init_repo(path: Path) -> None:
    run_git(path, "init", "-b", "master")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test User")
    date = "2020-01-01T00:00:00+00:00"
    run_git(
        path,
        "commit",
        "--allow-empty",
        "-m",
        "root",
        env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )


def test_watch_is_remote_scoped_and_reverse_chronological(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

    origin = tmp_path / "origin.git"
    backup = tmp_path / "backup.git"
    for remote in [origin, backup]:
        subprocess.run(
            ["git", "init", "--bare", str(remote)],
            check=True,
            capture_output=True,
        )
    run_git(repo, "remote", "add", "origin", str(origin))
    run_git(repo, "remote", "add", "backup", str(backup))

    run_git(repo, "switch", "-c", "alpha")
    date = "2021-01-01T00:00:00+00:00"
    run_git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "alpha work",
        env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )
    alpha = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "update-ref", "refs/remotes/origin/alpha", alpha)

    run_git(repo, "switch", "master")
    run_git(repo, "switch", "-c", "beta")
    date = "2022-01-01T00:00:00+00:00"
    run_git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "beta work",
        env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )
    beta = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "update-ref", "refs/remotes/origin/beta", beta)

    run_git(repo, "switch", "master")
    run_git(repo, "switch", "-c", "backup-only")
    date = "2030-01-01T00:00:00+00:00"
    run_git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "backup only",
        env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )
    backup_sha = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "update-ref", "refs/remotes/backup/backup-only", backup_sha)

    _, commits = collect_remote_commits(repo, "origin", limit=10)

    assert [commit.summary for commit in commits[:2]] == ["beta work", "alpha work"]
    assert commits[0].branch == "beta"
    assert commits[1].branch == "alpha"
    assert all(not commit.branch.startswith("origin/") for commit in commits)
    assert all(commit.summary != "backup only" for commit in commits)


def test_watch_fetches_only_requested_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

    origin = tmp_path / "origin.git"
    backup = tmp_path / "backup.git"
    for remote in [origin, backup]:
        subprocess.run(
            ["git", "init", "--bare", str(remote)],
            check=True,
            capture_output=True,
        )
    run_git(repo, "remote", "add", "origin", str(origin))
    run_git(repo, "remote", "add", "backup", str(backup))
    run_git(repo, "push", "origin", "master")
    run_git(repo, "push", "backup", "master")

    run_git(repo, "switch", "-c", "origin-new")
    run_git(repo, "commit", "--allow-empty", "-m", "origin new")
    origin_sha = run_git(repo, "rev-parse", "HEAD")
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(origin),
            "update-ref",
            "refs/heads/origin-new",
            origin_sha,
        ],
        check=True,
        capture_output=True,
    )

    run_git(repo, "switch", "master")
    run_git(repo, "switch", "-c", "backup-new")
    run_git(repo, "commit", "--allow-empty", "-m", "backup new")
    backup_sha = run_git(repo, "rev-parse", "HEAD")
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(backup),
            "update-ref",
            "refs/heads/backup-new",
            backup_sha,
        ],
        check=True,
        capture_output=True,
    )

    run_git(repo, "update-ref", "-d", "refs/remotes/origin/origin-new")
    run_git(repo, "update-ref", "-d", "refs/remotes/backup/backup-new")

    fetch = FetchStatus()
    assert _start_fetch(repo, "origin", fetch)
    for _ in range(200):
        if _poll_fetch(fetch):
            break
        time.sleep(0.01)

    assert fetch.state == "ok"
    assert run_git(repo, "rev-parse", "refs/remotes/origin/origin-new") == origin_sha
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "refs/remotes/backup/backup-new"],
            cwd=repo,
            capture_output=True,
        ).returncode
        != 0
    )
