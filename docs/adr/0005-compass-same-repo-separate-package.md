# 0005: Vault Compass lives in this repo as a separate package

Status: accepted (2026-10-06)

## Context

kai was pruned back to ingestion after 13 analysis commands went unused (see CHANGELOG, issue #29).
Vault Compass brings analysis back, as a visual app rather than CLI commands. Putting it in a new
repo would keep kai clean, but would mean copying the quality pipeline: CI workflow, a pre-commit
config with 9 hooks, and the radon, CRAP, secret-scan and test scripts (about 1,000 lines) — and
keeping two copies in sync.

## Decision

- Same repo, separate package: `src/vault_compass/` (FastAPI backend) next to
  `src/obsidian_ai_tools/`, and `web/` for the Next.js + assistant-ui frontend.
- Its own entry point: `compass serve`. No new `kai` subcommands.
- An import rule, enforced by `import-linter` as a pre-commit hook and in CI: `vault_compass` may
  import `obsidian_ai_tools`; `obsidian_ai_tools` never imports `vault_compass`.
- The Python side uses the existing gates unchanged (ruff, mypy, bandit, radon, CRAP, pytest,
  80% coverage floor).
- `web/` gets its own checks (tsc, eslint, tests) and CI path filters, so frontend changes don't
  run the Python suite and vice versa.

## Consequences

- One pipeline to maintain; compass can reuse kai's frontmatter and wikilink parsing directly.
- kai's ingest-only scope is protected by the import rule and the separate command, not by a repo
  boundary. A change to kai that compass needs must still make sense for kai on its own.
- The repo gains a Node toolchain. Python-only contributors (agents included) need to know `web/`
  has its own setup; document it in `DEVELOPMENT.md` and `AGENTS.md`.

## Revisit triggers

1. Compass needs a release cadence or versioning that conflicts with kai's.
2. The import rule is bypassed or kai starts gaining compass-only code.
