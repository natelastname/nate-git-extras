from importlib.metadata import distribution
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


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


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
