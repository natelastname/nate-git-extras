set shell := ["bash", "-cu"]

default:
    @just --list

install:
    poetry install

run *args:
    poetry run nate_git_ls {{args}}

test:
    poetry run pytest

test-verbose:
    poetry run pytest -vv

test-one path:
    poetry run pytest {{path}}

format:
    poetry run ruff format src tests

lint:
    poetry run ruff check src tests

lint-fix:
    poetry run ruff check --fix src tests

typecheck:
    poetry run basedpyright

docs:
    poetry run mkdocs build

docs-serve:
    poetry run mkdocs serve

docs-strict:
    poetry run mkdocs build --strict

fix:
    just format
    just lint-fix

check-fast:
    just lint
    just typecheck

check:
    just lint
    just typecheck
    just test

clean:
    find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
    find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
