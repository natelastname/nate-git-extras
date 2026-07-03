import nate_git_tree.nate_git_cp as nate_git_cp
import nate_git_tree.nate_git_ls as nate_git_ls


def test_nate_git_cp_has_main():
    assert hasattr(nate_git_cp, "main")


def test_nate_git_ls_has_main():
    assert hasattr(nate_git_ls, "main")
