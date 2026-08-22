# nate-git-extras

Small Git-aware filesystem utilities behind one Cyclopts CLI.

## Setup

```bash
uv sync --extra dev
```

## CLI

The package installs one entrypoint:

```bash
nate-git-extras COMMAND ...
```

The current subcommands are:

- `cp`: copy files or directories while respecting Git ignore rules
- `ls`: print a tree-style directory listing
- `status`: show a read-only branch merge/cleanup dashboard

### Copy

```bash
# Copy a directory into an existing directory (creates place1/folder1)
uv run nate-git-extras cp ./folder1/ /home/nate/place1/

# Copy shell-expanded sources directly into an existing directory
uv run nate-git-extras cp ./folder1/* /home/nate/place1/

# Copy a template directory's contents directly into the destination
uv run nate-git-extras cp --template ./template/ ./new-project/

# Preview a copy
uv run nate-git-extras cp --dry-run ./folder1/ /home/nate/place1/
```

### List

```bash
# Print a tree of the current directory, excluding ignored paths
uv run nate-git-extras ls

# Show ignored entries without descending into ignored directories
uv run nate-git-extras ls --include-ignored

# Show and descend into ignored directories
uv run nate-git-extras ls --traverse-ignored
```

### Status

```bash
# Compare local branches with master
uv run nate-git-extras status

# Inspect another repository or base branch
uv run nate-git-extras status /path/to/repo --base main

# Change the inactivity threshold
uv run nate-git-extras status --stale-days 7

# Use a full-screen live dashboard, refreshing every two seconds
uv run nate-git-extras status --watch

# Choose a faster/slower watch cadence
uv run nate-git-extras status --watch --interval 0.5
```

`status` does not fetch, checkout, merge, or delete anything. It evaluates the local
refs currently in the repository and shows:

- `READY`: the branch tip can fast-forward or merge cleanly into the base
- `CONFLICT`: a trial merge reports conflicts
- `MERGED`: the branch is already contained in the base
- `ABSORBED`: the branch's patches are already represented in the base
- ahead/behind counts, tip activity age, stale branches, and checked-out/dirty
  worktrees

`--watch` uses the terminal's alternate screen, so the dashboard starts at the top of
the terminal and occupies the screen without scrolling your shell history. The
summary line is pinned to the bottom of the screen. Press `q` or `Ctrl-C` to exit and
return to the previous terminal contents. Watch mode remains read-only.

Cyclopts provides command-specific help:

```bash
uv run nate-git-extras --help
uv run nate-git-extras cp --help
uv run nate-git-extras ls --help
uv run nate-git-extras status --help
```

## Tests

```bash
uv run python -m pytest
```
