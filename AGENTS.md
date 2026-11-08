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
| bandit, gitleaks, README check, import-linter, radon | pre-commit | same |
| **mypy** | **pre-push** | `uv run mypy src --config-file mypy.ini` |
| **pytest full coverage (parallel)** | **pre-push** | `COVERAGE=1 ./scripts/test.sh -q` |
| **CRAP gate** | **pre-push** | `./scripts/crap_gate.sh` (after the coverage run) |

No tests run on `git commit`. Tests, mypy and the CRAP gate run on `git push`. Run them manually before pushing, or with `uv run pre-commit run --hook-stage pre-push --all-files`:

```bash
uv run mypy src --config-file mypy.ini
COVERAGE=1 ./scripts/test.sh -q   # parallel (pytest-xdist), must be ≥ 80%
./scripts/crap_gate.sh
```

`./scripts/test.sh` runs tests across all CPU cores. Set `PYTEST_WORKERS=0` to run them one at a time when debugging a single failure.

CI runs once per pull request (not again on merge to `main`): pre-commit hooks, mypy, parallel tests with coverage, the CRAP gate, and the Gitleaks security job. Each check runs once.

Coverage floor is 80% (`fail_under = 80` in `pyproject.toml`). New modules must include tests for all non-trivial paths — especially LLM-calling functions, which need a mocked client test (patch `OpenAI` at `obsidian_ai_tools.<module>.OpenAI`).

### Common E501 (line > 100 chars) patterns to watch for

These patterns routinely blow the 100-char limit — keep them short or split them:

- `typer.echo()` with f-strings that embed a path or long message
- `logger.warning("... %s", long_variable)` — wrap the string across two lines
- Test assertions: `assert "long literal string" in content` — extract into a variable first
- Patch target strings in tests: `"obsidian_ai_tools.commands.module.function_name"`

### Never switch scripts/run_radon.sh or .pre-commit-config.yaml to `uv run`

These files must use plain `python`/`python3` or `.venv/bin/...` — not `uv run` — so the `language: system` hooks run the same way in every checkout and worktree. Changing which hooks run is fine when an issue asks for it; changing how they invoke Python is not.

### Import hygiene for new files

- **Sort order**: stdlib → third-party → first-party, alphabetical within each group. Ruff enforces this (I001).
- **No speculative imports**: only import what you actually use in the file. Unused imports fail F401.
- When scaffolding a new module, add imports as you write the code that needs them, not all at once up front.

### Why this matters for refactors touching test files

When renaming mock patch paths (e.g. `obsidian_ai_tools.cli.X` → `obsidian_ai_tools.commands.Y.X`), the new strings are often longer and may push lines past the 100-char limit. `ruff format` will auto-fix them, but `make check` / `ruff check` (lint) will not catch formatting-only violations in all configurations. Always verify with pre-commit.

## Vault Compass (`src/vault_compass/`, `web/`)

A second app in this repo (ADR 0005), started with `compass serve`. Details: DEVELOPMENT.md ("Vault Compass").

- **Import rule:** `vault_compass` may import `obsidian_ai_tools`; `obsidian_ai_tools` must never import `vault_compass`. The `import-linter` hook and CI step fail the build otherwise (`uv run lint-imports`).
- Do not add compass features as `kai` subcommands. kai stays ingest-only.
- Compass writes only to its own DuckDB file (`compass_db_path`), never to kai's `observability.duckdb` (ADR 0006).
- `web/` is a Node project with its own setup (`cd web && npm install`); the Python gates do not cover it. Run `npm run typecheck && npm run lint && npm test` there; CI runs them from `.github/workflows/web.yml` on `web/` changes.

## Writing Tests

All new tests must follow the mutation-resistant test rules in DEVELOPMENT.md ("Writing mutation-resistant tests"). Summary: exact assertions (`==`, never substring/presence checks), `assert_called_with` on every mock, boundary values at exact thresholds, exercise defaults, exact log-record and persisted-content assertions, deterministic clocks. Rationale: mutation testing (see DEVELOPMENT.md) showed weaker patterns let defects survive.

## Parallel Agents

When dispatching multiple concurrent agents, give each its own branch and worktree (`.worktrees/<name>`) and spawn it with its `cwd` set to the worktree. Never run two agents in the same checkout, never switch branches mid-flight — a shared checkout caused real interference during the #50/#52 work. Commands, bootstrap, and cleanup: DEVELOPMENT.md ("Parallel agent worktrees").

