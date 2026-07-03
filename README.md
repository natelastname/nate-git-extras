# nate_git_tree

Small utilities for working with Git repositories.

## Setup

```bash
uv sync --extra dev
```

## CLI tools

Two entry points are provided once the package is installed:

- `nate_git_cp`: git-aware recursive copy that respects `.gitignore`
- `nate_git_ls`: tree-style listing that can optionally respect `.gitignore`

### Examples

```bash
# Print a tree of the current repo
uv run nate_git_ls

# Copy a template directory from this repo to a new location
uv run nate_git_cp path/to/src path/to/dest

# Copy a directory into an existing directory (creates place1/folder1)
uv run nate_git_cp ./folder1/ /home/nate/place1/

# Copy the contents of a directory into an existing directory
# (the shell expands ./folder1/* before it reaches nate_git_cp)
uv run nate_git_cp ./folder1/* /home/nate/place1/
```

## Tests

```bash
uv run python -m pytest
```
