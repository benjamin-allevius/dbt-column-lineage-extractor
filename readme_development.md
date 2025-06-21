## Development Guide

### Unit tests

Run unit tests with:
```bash
uv run -m pytest
```

### Code Quality

This project uses [Ruff](https://docs.astral.sh/ruff/) for code linting and formatting.

#### Linting

Check your code for issues:
```bash
uv run ruff check .
```

Auto-fix issues where possible:
```bash
uv run ruff check --fix .
```

#### Formatting

Format your code:
```bash
uv run ruff format .
```

#### Run both linting and formatting

For a complete code quality check:
```bash
uv run ruff check --fix .
uv run ruff format .
```

### Pre-commit Hooks

This project includes pre-commit hooks that will automatically:
- Keep your `uv.lock` file in sync with `pyproject.toml`
- Format and lint your code with Ruff before each commit

#### Install pre-commit hooks

```bash
uvx pre-commit install
```

#### Run pre-commit hooks manually

To run all hooks on all files:
```bash
uvx pre-commit run --all-files
```

To run hooks on staged files only:
```bash
uvx pre-commit run
```

Once installed, the hooks will run automatically before each `git commit`. If any issues are found, the commit will be blocked until you fix them.

### Build

Build the package:
```bash
uv build
```

### Development Setup

1. Clone the repository
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Install pre-commit hooks:
   ```bash
   uvx pre-commit install
   ```
4. Run tests to ensure everything works:
   ```bash
   uv run -m pytest
   ```
5. Your code will now be automatically formatted and linted before each commit!
