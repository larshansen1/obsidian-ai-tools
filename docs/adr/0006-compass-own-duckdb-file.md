# 0006: Vault Compass uses its own DuckDB file and reads kai's read-only

Status: proposed (2026-10-06)

## Context

kai writes observability data to `{vault_path}/.kai/observability.duckdb` from both the CLI and
`kai serve`. Vault Compass needs to store content tables (notes, tags, links, topics), cached AI
results (claims, source types) and its own usage log. DuckDB allows only one process to open a
file for writing at a time, so a long-running `compass serve` writing to kai's file would block
`kai ingest`.

## Decision

- Compass writes only to `{vault_path}/.kai/compass.duckdb`.
- It reads kai's tables by attaching `observability.duckdb` read-only, and only for short queries.
- Compass never writes to `observability.duckdb`.

## Consequences

- kai and compass can run at the same time without lock conflicts.
- Queries that join content with kai's cost or ingest data go through the attached database.
- If kai holds its write lock at the moment compass attaches, compass must retry or show
  slightly stale numbers rather than fail.

## Revisit triggers

1. kai's observability writes move to a different store.
2. Attaching read-only proves unreliable while kai is writing.
