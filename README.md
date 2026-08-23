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
- `status`: show a branch merge/cleanup dashboard

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

# Use the full-screen live dashboard
uv run nate-git-extras status --watch

# In watch mode, fetch/display remote branches every 30 seconds
uv run nate-git-extras status --watch --interval 30
```

`status` evaluates the refs currently in the repository and shows:

- `BASE`: the comparison base itself, always shown as the first row
- `READY`: the branch tip can fast-forward or merge cleanly into the base
- `CONFLICT`: a trial merge reports conflicts
- `MERGED`: the branch is already contained in the base
- `ABSORBED`: the branch's patches are already represented in the base
- ahead/behind counts, tip activity age, stale branches, and checked-out/dirty
  worktrees

The base row comes first. Remaining rows are sorted with local branches first, then
remote refs, with the most recently active branch first within each group. A remote
tracking ref that is identical to its local branch is omitted; remote-only or diverged
remote refs remain visible. This means a changed `origin/master` appears separately
from the local `master` base row after a remote refresh.

By default, only local branches are displayed and `status` does not access the
network. In `--watch` mode, press `g` to run one asynchronous `git fetch --all --prune`
and take one snapshot of the remote branches. Those remote rows remain frozen while
normal watch refreshes continue to update local branches only. Press `g` again to
refresh the remote snapshot. Passing `--interval N` instead fetches and refreshes the
remote snapshot automatically every N seconds.

The bottom status line distinguishes `fetching…` from the one-time remote status
calculation and reports `fetch ok` or a persistent fetch failure message.

In watch mode, use the up/down arrow keys to select a row and press `m` to merge a
`READY` branch into the base. The dashboard asks for a single-key `y/N` confirmation.
Fast-forwardable branches use `git merge --ff-only`; other clean merges use
`git merge --no-edit`. The base must be checked out in a clean worktree, and the
mergeability is revalidated immediately before Git changes the base. Remote-only rows
can be merged from their fetched remote-tracking ref. Merge success or failure is
reported in the bottom status line and the dashboard is refreshed immediately.

`--watch` uses the terminal's alternate screen, so the dashboard starts at the top of
the terminal and occupies the screen without scrolling your shell history. The
summary line is pinned to the bottom of the screen. Press `q` or `Ctrl-C` to exit and
return to the previous terminal contents.

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
