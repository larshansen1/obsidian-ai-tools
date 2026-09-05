# Worker Brief: Fix PR #67 CI pipeline audit

## Goal
Fix the failing build on PR #67 and complete the remaining CI pipeline audit fixes for this single PR.

## Context
PR #67 (`maint/64-ci-audit`) already made partial changes:
- Trigger dedup (push→main + pull_request)
- Switched to `astral-sh/setup-uv` with caching
- Unified pre-commit-in-CI
- Dropped bandit from security job (covered by pre-commit)
- Dropped `fetch-depth: 0` from security job

But the build fails and some audit findings remain.

## Build failure to fix

**Root cause**: `pre-commit` is in `requirements-dev.txt` but NOT in `pyproject.toml`'s dev dependencies. `uv sync` only installs from `pyproject.toml`, so `pre-commit` is missing in CI.

**Fix**: Add `pre-commit` to `[dependency-groups]` dev in `pyproject.toml`.

Also: the local pre-commit hooks reference `.venv/bin/python` for the README check — in CI with uv this path may not exist. The hook should use `python3` (or just `python`) instead. This hook entry:
```yaml
entry: .venv/bin/python scripts/check_readme_commands.py
```

## What else to fix (from issue #64 audit)

### Merge conflict
The branch has a merge conflict with `origin/main` in `.github/workflows/ci.yml`. Rebase onto latest `origin/main` and resolve.

### mypy version drift
The pre-commit mypy hook pins `v1.19.1`, but CI runs `uv run mypy src` directly (not through pre-commit), so CI gets the latest mypy from `uv sync`. 

**Fix**: Run mypy through pre-commit too, or pin mypy version in pyproject.toml dev deps. Simplest fix: also run mypy via pre-commit: change the CI step from:
```yaml
- name: Typecheck (mypy)
  run: uv run mypy src --config-file mypy.ini
```
to a pre-commit run for the pre-push stage. However, `pre-commit run --all-files` only runs hooks for the pre-commit stage by default. You'd need `--hook-stage pre-push` to run mypy. 

Safest minimal fix: add mypy with a pinned version in pyproject.toml dev deps so `uv sync` gives a consistent version.

### Security job: gitleaks curl install
The security job still curl-installs gitleaks. This is acceptable if we want full-tree scanning (wider scope than pre-commit's staged-only scan). But:
- Replace the curl|tar with the official `gitleaks/gitleaks-action@v2` action for a cleaner approach

### README check hook CI compatibility
The local hook uses `entry: .venv/bin/python` which won't exist in CI. Fix the entry to use plain `python3` (as done in `scripts/run_radon.sh` pattern per AGENTS.md rules about not using `uv run` in hooks that need to work in both places).

## Constraints from AGENTS.md
- Run `uv run pre-commit run --all-files` after all changes
- Run `uv run mypy src --config-file mypy.ini` before push
- Run `COVERAGE=1 ./scripts/test.sh -q` before push
- Never modify `scripts/run_radon.sh` or `.pre-commit-config.yaml`
- Line length 100 char max
- Import order: stdlib → third-party → first-party

## Acceptance criteria
1. Build job passes in CI (all steps green)
2. Security job passes in CI
3. `on: [push, pull_request]` changed to `pull_request` + `push` restricted to `main`
4. `uv sync` installs all tools (including pre-commit and pinned mypy)
5. No merge conflicts with main
6. PR title and description are updated to reflect the full scope

## Verification
```bash
cd /home/lars/.pi-bot/repos/larshansen1-obsidian-ai-tools/.worktrees/ci-audit
uv run pre-commit run --all-files
uv run mypy src --config-file mypy.ini
COVERAGE=1 ./scripts/test.sh -q
```

## Issue reference
Closes #64