## Quality Checks

**Always run `pre-commit run --all-files` as the final gate — not `make check`.**

`make check` runs lint + typecheck + radon + bandit + tests, but misses the `ruff format` hook that pre-commit enforces. When `ruff format` auto-fixes files and returns non-zero, the commit is blocked. Running `make check` alone gives a false green.

### Correct workflow

```
# After making code changes:
uv run pre-commit run --all-files

# If ruff-format modified files, stage them and re-run:
git add -u
uv run pre-commit run --all-files   # must pass clean (no files modified)
```

### Hook stages — what runs when

| Hook | Stage | Command to run manually |
|------|-------|------------------------|
| ruff lint + format | pre-commit | `uv run pre-commit run --all-files` |
| bandit, gitleaks, README check, radon, pytest-fast | pre-commit | same |
| **mypy** | **pre-push** | `uv run mypy src --config-file mypy.ini` |
| **pytest full coverage** | **pre-push** | `COVERAGE=1 ./scripts/test.sh -q` |

mypy and coverage only run on `git push`, not `git commit`. Always run both manually before pushing:

```bash
uv run mypy src --config-file mypy.ini
COVERAGE=1 ./scripts/test.sh -q   # must be ≥ 80%
```

Coverage floor is 80% (`fail_under = 80` in `pyproject.toml`). New modules must include tests for all non-trivial paths — especially LLM-calling functions, which need a mocked client test (patch `OpenAI` at `obsidian_ai_tools.<module>.OpenAI`).

### Common E501 (line > 100 chars) patterns to watch for

These patterns routinely blow the 100-char limit — keep them short or split them:

- `typer.echo()` with f-strings that embed a path or long message
- `logger.warning("... %s", long_variable)` — wrap the string across two lines
- Test assertions: `assert "long literal string" in content` — extract into a variable first
- Patch target strings in tests: `"obsidian_ai_tools.commands.module.function_name"`

### Never modify scripts/run_radon.sh or .pre-commit-config.yaml

These files must use plain `python`/`python3` — not `uv run` — because they run both locally (where the venv is active) and in CI (which has no `uv`). Changing them to use `uv run` will break CI with "uv: command not found".

### Import hygiene for new files

- **Sort order**: stdlib → third-party → first-party, alphabetical within each group. Ruff enforces this (I001).
- **No speculative imports**: only import what you actually use in the file. Unused imports fail F401.
- When scaffolding a new module, add imports as you write the code that needs them, not all at once up front.

### Why this matters for refactors touching test files

When renaming mock patch paths (e.g. `obsidian_ai_tools.cli.X` → `obsidian_ai_tools.commands.Y.X`), the new strings are often longer and may push lines past the 100-char limit. `ruff format` will auto-fix them, but `make check` / `ruff check` (lint) will not catch formatting-only violations in all configurations. Always verify with pre-commit.

## Writing Tests

All new tests must follow the mutation-resistant test rules in DEVELOPMENT.md ("Writing mutation-resistant tests"). Summary: exact assertions (`==`, never substring/presence checks), `assert_called_with` on every mock, boundary values at exact thresholds, exercise defaults, exact log-record and persisted-content assertions, deterministic clocks. Rationale: mutation testing (see DEVELOPMENT.md) showed weaker patterns let defects survive.
