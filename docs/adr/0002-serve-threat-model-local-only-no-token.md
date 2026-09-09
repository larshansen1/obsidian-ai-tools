# 0002: Serve threat model — local-only, no shared-secret token

Status: accepted (2026-09-09)

## Context

Issue [#101](https://github.com/larshansen1/obsidian-ai-tools/issues/101) reported two concrete holes
found by `scripts/fuzz_webhook.py`:

- All endpoints (`/status`, `/lookup`, `/ingest`) are unauthenticated.
- `/status` discloses the absolute vault path, the inbox folder, and the LLM model — server
  fingerprinting that makes a remote attacker's job easier.

The fuzz result was only reachable over the network because `kai serve --host 0.0.0.0` was permitted
with nothing more than a stderr warning.

## Decision

- No shared-secret token. The boundary is the loopback bind, not an HTTP credential.
- Default bind stays `127.0.0.1`; `kai serve` refuses any non-loopback `--host` with exit code 1
  unless `--i-know-what-im-doing` is passed as an explicit acknowledgment.
- `/status` returns only `{"running": true}` — no vault path, no inbox folder, no model.

Rationale for skipping the token: the only consumer is the Chrome extension, which has no channel to
receive a server-generated secret (no native messaging host; wiring one is a separate project). And a
token file would add no security against the actual threat set — any local process able to read the
token file can equally read `~/.kai/.env`, which already holds `OPENROUTER_API_KEY` and
`GITHUB_TOKEN`. Authentication only becomes meaningful across a trust boundary the loopback bind
already closes.

## Consequences

- Same-user local processes can still call `/lookup` and `/ingest` with no auth. Accepted: that is
  the intended use case (Chrome extension → local daemon), and the risk is bounded to the user's own
  session.
- `/status` no longer exposes configuration; the Chrome extension's `checkServer` only reads `r.ok`,
  so its behavior is unchanged.
- A non-loopback bind is now a two-flag decision (`--host 0.0.0.0 --i-know-what-im-doing`) that
  will not happen by accident, and shows up in shell history and the process list.

## Revisit triggers

1. A real multi-user or remote deployment requirement (a second machine consuming the API, a shared
   CI box, exposing the service behind a reverse proxy).
2. A native messaging host is added to the Chrome extension, giving it a secure channel to receive a
   server-generated secret.

If revisited, add an HTTP-level credential with a per-user secret and mandatory TLS, not a static
token in a world-readable file.