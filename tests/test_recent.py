from pathlib import Path
import os
import subprocess

from nate_git_extras.cli import app


def run_cli(*args: str) -> None:
    app(
        list(args),
        exit_on_error=False,
        print_error=False,
        result_action="return_value",
    )


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
        "initial",
        env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )


def test_recent_orders_commits_and_attributes_branches(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

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
    alpha_sha = run_git(repo, "rev-parse", "HEAD")[:7]

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
    beta_sha = run_git(repo, "rev-parse", "HEAD")[:7]

    run_cli("recent", str(repo), "--limit", "2")
    output = capsys.readouterr().out

    assert output.index("beta work") < output.index("alpha work")
    assert beta_sha in output and alpha_sha in output
    assert "beta" in output and "alpha" in output
    assert "showing 2 commits" in output


def test_recent_fetch_discovers_remote_only_commit(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
    )
    run_git(repo, "remote", "add", "origin", str(remote))
    run_git(repo, "push", "origin", "master")

    run_git(repo, "switch", "-c", "agent-work")
    run_git(repo, "commit", "--allow-empty", "-m", "remote agent result")
    remote_sha = run_git(repo, "rev-parse", "HEAD")
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote),
            "update-ref",
            "refs/heads/agent-work",
            remote_sha,
        ],
        check=True,
        capture_output=True,
    )
    run_git(repo, "switch", "master")
    run_git(repo, "branch", "-D", "agent-work")
    run_git(repo, "update-ref", "-d", "refs/remotes/origin/agent-work")

    run_cli("recent", str(repo), "--fetch")
    output = capsys.readouterr().out

    assert "remote agent result" in output
    assert "origin/agent-work" in output
    assert remote_sha[:7] in output
