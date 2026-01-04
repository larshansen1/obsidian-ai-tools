# Decisions & Learnings — Tracer Bullet Cycle 1

## Date
2026-01-02

---

## What Worked

### CLI-First Approach
The console application (`kai ingest`) proved to be a good fit:
- Simple, composable interface
- Easy to script and automate
- Can be driven by coding agents (Claude, Antigravity, etc.)
- No UI overhead during rapid iteration

### Multi-Provider Fallback
The transcript provider hierarchy works well:
1. **Supadata** (primary, paid, reliable)
2. **youtube-transcript-api** (free, with circuit breaker)
3. **Decodo** (last resort)

Caching prevents burning API quota on repeated requests.

### Prompt V2 Improvements
The `youtube_v2` prompt with claims/implications sections significantly improved note usefulness:
- Baseline v1: **9.4/15** average
- Final v2: **14.8/15** average (+5.4 improvement)

---

## What Surprised Me

### YouTube Scraping Complexity
Getting reliable transcript fetching was harder than expected:
- Rate limiting from YouTube's unofficial API
- One proxy provider's service didn't work at all
- Library API changed between versions (`get_transcript` → `fetch`)
- Needed circuit breaker pattern for resilience

### Template Escaping
Python `str.format()` requires `{{` `}}` to produce literal braces in prompts with JSON examples. This broke the v2 prompt initially.

### YAML Edge Cases
Titles with colons (common in video titles) break YAML parsing unless quoted. Required adding a `_yaml_escape()` helper.

---

## What I'd Do Differently in Cycle 2

1. **Use YAML library for frontmatter** — Instead of string formatting, use `yaml.dump()` for guaranteed valid YAML output

2. **Provider abstraction** — Create a unified provider interface with consistent error handling, rate limiting, and health checks

3. **Tag taxonomy from vault** — Build tag suggestions from existing vault tags earlier in the workflow

---

## Postponed to Backlog

### MCP Integration
- Tool definitions for Open WebUI
- Conversational "Ingest this video" trigger
- **Reason**: CLI ergonomics sufficient for now, can drive automation via coding agents

### Provider Strategy
- Consistent rate limiting across all providers
- Smart provider selection (cost/reliability tradeoffs)
- Health monitoring and automatic failover tuning

### Tag Normalization
- Controlled vocabulary enforcement
- Deduplication (ai vs artificial-intelligence)
- Needs more vault data to tune

---

## Tracer Bullet Status

| Criterion | Status |
|-----------|--------|
| ✅ Ingest video → note in vault | Complete |
| ✅ Search by tag/keyword | Complete |
| ✅ 5+ notes evaluated | Complete (14.8/15 avg) |
| ✅ Provenance on all notes | Complete |
| ✅ Document learnings | This file |

**Verdict**: Tracer bullet complete. Ready for Cycle 2 when desired.
