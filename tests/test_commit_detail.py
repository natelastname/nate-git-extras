from contextlib import nullcontext
from pathlib import Path
import os
import subprocess

import nate_git_extras.recent as recent_module
from nate_git_extras.cli import app


def run_cli(*args: str) -> None:
    app(
        list(args),
        exit_on_error=False,
        print_error=False,
        result_action="return_value",
    )


def run_git(
    path: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> str:
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
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    run_git(path, "add", ".")
    run_git(path, "commit", "-m", "initial")


def add_detail_commit(repo: Path) -> str:
    run_git(repo, "switch", "-c", "feature/detail")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(
        repo,
        "commit",
        "-m",
        "Add commit detail view",
        "-m",
        "Explain what changed in this commit.",
    )
    return run_git(repo, "rev-parse", "HEAD")


def test_show_commit_detail(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    sha = add_detail_commit(repo)

    run_cli("show", sha, str(repo))
    output = capsys.readouterr().out

    assert sha[:12] in output
    assert "feature/detail" in output
    assert "Add commit detail view" in output
    assert "Explain what changed in this commit." in output
    assert "a.txt" in output and "b.txt" in output
    assert "+2 / -0" in output
    assert "master · not merged" in output


def test_recent_watch_drills_into_commit_and_file_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    add_detail_commit(repo)

    keys = iter(["enter", "enter", "q", "q", "q"])
    patches = 0
    real_patch = recent_module.commit_patch

    def commit_patch(*args, **kwargs):
        nonlocal patches
        patches += 1
        return real_patch(*args, **kwargs)

    monkeypatch.setattr(
        recent_module,
        "_watch_terminal",
        lambda _console: nullcontext(0),
    )
    monkeypatch.setattr(
        recent_module,
        "_read_key",
        lambda _fd, _timeout: next(keys),
    )
    monkeypatch.setattr(recent_module, "commit_patch", commit_patch)

    run_cli("recent", str(repo), "--watch")

    assert patches == 1
