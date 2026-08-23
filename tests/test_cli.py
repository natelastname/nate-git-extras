from contextlib import nullcontext
from importlib.metadata import distribution
from pathlib import Path
import os
import subprocess
import time

import nate_git_extras.status as status_module
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
    if env is not None:
        process_env.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env=process_env,
    )
    return result.stdout.strip()


def init_git_repo(path: Path) -> None:
    run_git(path, "init", "-b", "master")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test User")
    run_git(path, "commit", "--allow-empty", "-m", "initial")


def test_project_has_one_entrypoint() -> None:
    console_scripts = {}
    for entrypoint in distribution("nate-git-extras").entry_points:
        if entrypoint.group == "console_scripts":
            console_scripts[entrypoint.name] = entrypoint.value

    assert console_scripts == {"nate-git-extras": "nate_git_extras.cli:app"}


def test_cp_directory_into_existing_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    src = repo / "folder1"
    nested = src / "nested"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("hello", encoding="utf-8")

    dst_root = tmp_path / "place1"
    dst_root.mkdir()

    run_cli("cp", str(src), str(dst_root))

    dst_dir = dst_root / "folder1"
    assert dst_dir.is_dir()
    assert (dst_dir / "nested" / "file.txt").read_text(encoding="utf-8") == "hello"


def test_cp_glob_like_multiple_sources(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    src = repo / "folder1"
    src.mkdir()
    (src / "top.txt").write_text("top", encoding="utf-8")
    nested = src / "nested"
    nested.mkdir()
    (nested / "file.txt").write_text("nested", encoding="utf-8")

    dst_root = tmp_path / "place1"
    dst_root.mkdir()

    sources = sorted(src.iterdir())
    args = ["cp"]
    for source in sources:
        args.append(str(source))
    args.append(str(dst_root))
    run_cli(*args)

    assert not (dst_root / "folder1").exists()
    assert (dst_root / "top.txt").read_text(encoding="utf-8") == "top"
    assert (dst_root / "nested" / "file.txt").read_text(encoding="utf-8") == "nested"


def test_cp_template_mode_includes_dotdirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    src = repo / "template_src"
    src.mkdir()
    (src / "regular.txt").write_text("regular", encoding="utf-8")

    dot_dir = src / ".openhands"
    dot_dir.mkdir()
    (dot_dir / "config.yaml").write_text("dotdir", encoding="utf-8")
    (src / ".specify").write_text("dotfile", encoding="utf-8")

    dst_root = tmp_path / "place_template"
    run_cli("cp", "--template", str(src), str(dst_root))

    assert dst_root.is_dir()
    assert not (dst_root / "template_src").exists()
    assert (dst_root / "regular.txt").read_text(encoding="utf-8") == "regular"
    assert (dst_root / ".openhands" / "config.yaml").read_text(encoding="utf-8") == "dotdir"
    assert (dst_root / ".specify").read_text(encoding="utf-8") == "dotfile"


def test_cp_template_dry_run_does_not_create_destination(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    src = repo / "template_src"
    src.mkdir()
    (src / "file.txt").write_text("content", encoding="utf-8")

    dst_root = tmp_path / "place_template"
    run_cli("cp", "--template", "--dry-run", str(src), str(dst_root))

    assert not dst_root.exists()


def test_ls_ignore_modes(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    (repo / ".gitignore").write_text("ignored/\nignored.txt\n", encoding="utf-8")
    (repo / "visible.txt").write_text("visible", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored", encoding="utf-8")
    ignored_dir = repo / "ignored"
    ignored_dir.mkdir()
    (ignored_dir / "nested.txt").write_text("nested", encoding="utf-8")

    run_cli("ls", str(repo))
    default_output = capsys.readouterr().out
    assert "visible.txt" in default_output
    assert "ignored.txt" not in default_output
    assert "ignored" not in default_output

    run_cli("ls", str(repo), "--include-ignored")
    included_output = capsys.readouterr().out
    assert "ignored.txt" in included_output
    assert "ignored" in included_output
    assert "nested.txt" not in included_output

    run_cli("ls", str(repo), "--traverse-ignored")
    traversed_output = capsys.readouterr().out
    assert "ignored.txt" in traversed_output
    assert "nested.txt" in traversed_output


def test_status_dashboard_classifies_branches(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    run_git(repo, "switch", "-c", "merged")
    (repo / "merged.txt").write_text("merged\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "merged work")
    run_git(repo, "switch", "master")
    run_git(repo, "merge", "--ff-only", "merged")

    run_git(repo, "switch", "-c", "absorbed")
    (repo / "absorbed.txt").write_text("absorbed\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "absorbed work")
    absorbed_sha = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "master")
    (repo / "master-only.txt").write_text("master\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "master only")
    run_git(repo, "cherry-pick", absorbed_sha)

    run_git(repo, "switch", "-c", "ready")
    (repo / "ready.txt").write_text("ready\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "ready work")
    run_git(repo, "switch", "master")

    run_git(repo, "switch", "-c", "conflicting")
    (repo / "shared.txt").write_text("branch\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "branch edit")
    run_git(repo, "switch", "master")
    (repo / "shared.txt").write_text("master\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "master edit")

    run_cli("status", str(repo), "--stale-days", "9999")
    output = capsys.readouterr().out

    assert "READY" in output and "ready" in output
    assert "CONFLICT" in output and "conflicting" in output
    assert "MERGED" in output and "merged" in output
    assert "ABSORBED" in output and "absorbed" in output
    assert "conflict" in output
    assert "cleanup" in output


def test_status_dashboard_marks_stale_and_dirty_worktrees(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    run_git(repo, "switch", "-c", "old-agent")
    (repo / "old.txt").write_text("old\n", encoding="utf-8")
    run_git(repo, "add", ".")
    old_date = "2000-01-01T00:00:00+00:00"
    run_git(
        repo,
        "commit",
        "-m",
        "old work",
        env={"GIT_AUTHOR_DATE": old_date, "GIT_COMMITTER_DATE": old_date},
    )
    run_git(repo, "switch", "master")

    worktree = tmp_path / "agent-worktree"
    run_git(repo, "worktree", "add", str(worktree), "old-agent")
    (worktree / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

    run_cli("status", str(repo))
    output = capsys.readouterr().out

    assert "old-agent" in output
    assert "stale" in output
    assert "dirty" in output


def test_status_sorts_local_then_remote_by_activity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    for name, date in [
        ("local-old", "2020-01-01T00:00:00+00:00"),
        ("local-new", "2021-01-01T00:00:00+00:00"),
    ]:
        run_git(repo, "switch", "-c", name)
        run_git(
            repo,
            "commit",
            "--allow-empty",
            "-m",
            name,
            env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
        )
        run_git(repo, "switch", "master")

    run_git(repo, "switch", "-c", "remote-source")
    remote_date = "2030-01-01T00:00:00+00:00"
    run_git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "remote-only",
        env={"GIT_AUTHOR_DATE": remote_date, "GIT_COMMITTER_DATE": remote_date},
    )
    remote_sha = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "master")
    run_git(repo, "update-ref", "refs/remotes/origin/remote-only", remote_sha)
    run_git(repo, "branch", "-D", "remote-source")
    run_git(repo, "update-ref", "refs/remotes/origin/local-new", "local-new")

    _, local_only = status_module.collect_branch_status(repo)
    _, with_remotes = status_module.collect_branch_status(repo, include_remotes=True)

    assert [branch.name for branch in local_only] == [
        "master",
        "local-new",
        "local-old",
    ]
    assert [branch.name for branch in with_remotes] == [
        "master",
        "local-new",
        "local-old",
        "origin/remote-only",
    ]
    assert with_remotes[-1].remote
    assert status_module._move_selection(with_remotes, "local-new", 1) == "local-old"
    assert status_module._move_selection(with_remotes, "local-old", -1) == "local-new"


def test_status_watch_parses_arrow_keys() -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\x1b[A")
    assert status_module._watch_key(read_fd, 0.1) == "up"
    os.close(read_fd)
    os.close(write_fd)

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\x1b[B")
    assert status_module._watch_key(read_fd, 0.1) == "down"
    os.close(read_fd)
    os.close(write_fd)


def test_status_watch_navigation_does_not_rescan_git(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    for name in ["one", "two"]:
        run_git(repo, "switch", "-c", name)
        run_git(repo, "commit", "--allow-empty", "-m", name)
        run_git(repo, "switch", "master")

    scans = 0
    real_collect = status_module.collect_branch_status

    def collect(*args, **kwargs):
        nonlocal scans
        scans += 1
        return real_collect(*args, **kwargs)

    keys = iter(["down", "up", "down", "q"])
    monkeypatch.setattr(status_module, "collect_branch_status", collect)
    monkeypatch.setattr(status_module, "_watch_terminal", lambda _console: nullcontext(0))
    monkeypatch.setattr(status_module, "_watch_key", lambda _fd, _timeout: next(keys))

    run_cli("status", str(repo), "--watch")

    assert scans == 1


def test_status_watch_fetch_controls(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    fetches = 0
    keys = iter(["g", "q"])

    def start_fetch(_root: Path, fetch: status_module.FetchStatus) -> bool:
        nonlocal fetches
        fetches += 1
        fetch.state = "fetching"
        return True

    monkeypatch.setattr(status_module, "_watch_terminal", lambda _console: nullcontext(0))
    monkeypatch.setattr(status_module, "_watch_key", lambda _fd, _timeout: next(keys))
    monkeypatch.setattr(status_module, "_start_fetch", start_fetch)

    run_cli("status", str(repo), "--watch")
    assert fetches == 1

    fetches = 0
    monkeypatch.setattr(status_module, "_watch_key", lambda _fd, _timeout: "q")
    run_cli("status", str(repo), "--watch", "--interval", "60")
    assert fetches == 1


def test_fetch_status_reports_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    run_git(repo, "remote", "add", "origin", str(tmp_path / "missing.git"))

    fetch = status_module.FetchStatus()
    status_module._start_fetch(repo, fetch)
    for _ in range(100):
        if status_module._poll_fetch(fetch):
            break
        time.sleep(0.01)

    assert fetch.state == "failed"
    assert fetch.detail.startswith("fatal:")
