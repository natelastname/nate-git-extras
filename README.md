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
- `branches`: list local and remote branches grouped by where they exist

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

### Branches

```bash
# Show local branches, remote branches, and each remote's default branch
uv run nate-git-extras branches

# Refresh remote-tracking refs first
uv run nate-git-extras branches --fetch

# Disable automatic paging or terminal colors when needed
uv run nate-git-extras branches --no-pager
uv run nate-git-extras branches --no-color
```

Branch output is colorized on terminals: the current branch is green, other local
branches are cyan, remote refs are magenta, tracked status is green, and remote-only
or untracked status is yellow. If the rendered output is taller than the terminal,
it is automatically sent through the system pager. Piped output is never paged and
Rich automatically suppresses terminal color codes when stdout is not a terminal.

Example output:

```text
Local branches
  * master -> origin/master
    feature/local

Remote branches
    origin/master       [tracked by master]
    origin/remote-only  [remote only]

Remote defaults
    origin/HEAD -> origin/master
```

Cyclopts provides command-specific help:

```bash
uv run nate-git-extras --help
uv run nate-git-extras cp --help
uv run nate-git-extras ls --help
uv run nate-git-extras branches --help
```

## Tests

```bash
uv run python -m pytest
```
