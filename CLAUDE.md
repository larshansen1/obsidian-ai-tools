# Writing Tests

All new tests must follow the mutation-resistant test rules in DEVELOPMENT.md ("Writing mutation-resistant tests"). Summary: exact assertions (`==`, never substring/presence checks), `assert_called_with` on every mock, boundary values at exact thresholds, exercise defaults, exact log-record and persisted-content assertions, deterministic clocks. Rationale: mutation testing (see DEVELOPMENT.md) showed weaker patterns let defects survive.

See AGENTS.md for quality-gate rules.