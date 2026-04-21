# AGENTS Instructions

goose_template is a Python project using Poetry, Ruff, Pytest, and BasedPyright.

## Setup

```bash
poetry install
```

## Commands

### Run

```bash
just run [args] # run CLI entrypoint
```

### Test

```bash
just test # run all tests
just test-one path # run specific test file
```

### Lint / Format

```bash
just format # format code (ruff format)
just lint # lint code (ruff check)
just lint-fix # auto-fix lint issues
```

### Type Check

```bash
just typecheck # basedpyright (LSP-level checks)
```

### Validation

```bash
just fix # format + lint-fix
just check-fast # lint + typecheck
just check # lint + typecheck + test
```

## Structure

```text
src/goose_template/ # main package
tests/ # test suite
pyproject.toml # project config
justfile # command runner
```

## Development Loop

1. Make changes
2. Run `just fix`

## Rules

- Test: All new behavior must have tests in `tests/`.
- Test: Bug fixes must include regression tests.
- Lint: Code must pass `just lint`.
- Format: Code must pass `just format`.
- Imports: Imports must be clean and sorted (enforced by Ruff).
- CLI: Entry point is `goose_template.cli:cli`.
- Typing: BasedPyright is used primarily for editor support (type checking is minimal).

## Code Quality

### Comments

- Prefer self-documenting code over comments.
- Only explain /why/, not /what/.
 
### Simplicity

- Avoid unnecessary abstractions.
- Strive to be idiomatic as possible.
- Use for-loops rather than complicated list comprehensions
- Use modern Python features (3.10+)

### Exceptions

- Only use try/except deliberately. 
- Prefer loud failure over silent failure. 

### Logging

- Use `loguru`
- Do not add excessive logs

### Dependencies

- If the code depends on a third-party library, add it as an explicit dependency and import it normally.
- Do not hide required imports behind try/except to fake portability.

## Entry Points

- CLI: `src/goose_template/cli.py`
