from pathlib import Path
import subprocess

import nate_git_tree.nate_git_cp as nate_git_cp
import nate_git_tree.nate_git_ls as nate_git_ls


def test_nate_git_cp_has_main():
    assert hasattr(nate_git_cp, "main")



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



def test_nate_git_cp_directory_into_existing_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    src = repo / "folder1"
    nested = src / "nested"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("hello", encoding="utf-8")

    dst_root = tmp_path / "place1"
    dst_root.mkdir()

    nate_git_cp.main([str(src), str(dst_root)])

    dst_dir = dst_root / "folder1"
    assert dst_dir.is_dir()
    assert (dst_dir / "nested" / "file.txt").read_text(encoding="utf-8") == "hello"



def test_nate_git_cp_glob_like_multiple_sources(tmp_path):
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
    argv = [str(p) for p in sources] + [str(dst_root)]

    nate_git_cp.main(argv)

    # Contents of folder1/ should end up directly in place1/, not place1/folder1/.
    assert not (dst_root / "folder1").exists()
    assert (dst_root / "top.txt").read_text(encoding="utf-8") == "top"
    assert (dst_root / "nested" / "file.txt").read_text(encoding="utf-8") == "nested"




def test_nate_git_cp_template_mode_includes_dotdirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    src = repo / "template_src"
    src.mkdir()
    (src / "regular.txt").write_text("regular", encoding="utf-8")

    dot_dir = src / ".openhands"
    dot_dir.mkdir()
    (dot_dir / "config.yaml").write_text("dotdir", encoding="utf-8")

    dot_file = src / ".specify"
    dot_file.write_text("dotfile", encoding="utf-8")

    dst_root = tmp_path / "place_template"

    nate_git_cp.main(["--template", str(src), str(dst_root)])

    # Destination root should be created and populated with the contents of src,
    # not a nested template_src/ directory.
    assert dst_root.is_dir()
    assert not (dst_root / "template_src").exists()

    assert (dst_root / "regular.txt").read_text(encoding="utf-8") == "regular"
    assert (dst_root / ".openhands").is_dir()
    assert (dst_root / ".openhands" / "config.yaml").read_text(encoding="utf-8") == "dotdir"
    assert (dst_root / ".specify").read_text(encoding="utf-8") == "dotfile"


def test_nate_git_cp_template_mode_dry_run_does_not_create_destination(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    src = repo / "template_src"
    src.mkdir()
    (src / "file.txt").write_text("content", encoding="utf-8")

    dst_root = tmp_path / "place_template"

    nate_git_cp.main(["--template", "--dry-run", str(src), str(dst_root)])

    # Dry-run should not touch the filesystem at the destination.
    assert not dst_root.exists()

def test_nate_git_ls_has_main():
    assert hasattr(nate_git_ls, "main")
