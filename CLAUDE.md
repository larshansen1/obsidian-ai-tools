# Project Nickname

Short name for this repo: `kai`. Used for session titles, e.g. `kai-119`.

# Writing Tests

All new tests must follow the mutation-resistant test rules in DEVELOPMENT.md ("Writing mutation-resistant tests"). Summary: exact assertions (`==`, never substring/presence checks), `assert_called_with` on every mock, boundary values at exact thresholds, exercise defaults, exact log-record and persisted-content assertions, deterministic clocks. Rationale: mutation testing (see DEVELOPMENT.md) showed weaker patterns let defects survive.

Before committing or pushing, read AGENTS.md: quality gates, Vault Compass rules, and parallel-agent workflow (the latter is detailed in DEVELOPMENT.md, "Parallel agent worktrees").

# Outside Input

Work that takes outside input (web pages, files from other machines, model answers) or makes network calls: name what is untrusted and what leaves the machine. Threat model: docs/adr/0002-serve-threat-model-local-only-no-token.md.