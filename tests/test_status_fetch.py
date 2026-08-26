from pathlib import Path
import subprocess

from nate_git_extras.cli import app


def run_cli(*args: str) -> None:
    app(
        list(args),
        exit_on_error=False,
        print_error=False,
        result_action="return_value",
    )


def run_git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_status_fetch_shows_remote_only_branch(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "master")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "commit", "--allow-empty", "-m", "initial")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    run_git(repo, "remote", "add", "origin", str(remote))
    run_git(repo, "push", "origin", "master")

    run_git(repo, "switch", "-c", "remote-only")
    run_git(repo, "commit", "--allow-empty", "-m", "remote work")
    remote_sha = run_git(repo, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "--git-dir", str(remote), "update-ref", "refs/heads/remote-only", remote_sha],
        check=True,
        capture_output=True,
    )
    run_git(repo, "switch", "master")
    run_git(repo, "branch", "-D", "remote-only")
    run_git(repo, "update-ref", "-d", "refs/remotes/origin/remote-only")

    run_cli("status", str(repo), "--fetch")
    output = capsys.readouterr().out

    assert "master" in output
    assert "origin/remote-only" in output
