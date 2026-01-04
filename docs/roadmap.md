# Obsidian AI Pipeline — Roadmap

## Overview

This roadmap follows the **tracer bullet approach**: each cycle delivers thin, end-to-end slices that prove integration points before deepening any single area.

**Key principle**: Robustness is a cross-cutting concern included in EVERY cycle, not deferred to the end.

---

## Cycle Summary

| Cycle | Theme | Key Deliverables | Status |
|-------|-------|------------------|--------|
| **1** | Tracer Bullet | YouTube ingestion, CLI, search, eval baseline | ✅ Complete |
| **2** | Multi-Source Ingestion | Web URLs, markdown files, unified provider abstraction | ✅ Complete |
| **3** | Inbox Organization | Rule-based folder organization, batch processing | ✅ Complete |
| **3.5** | Vault Search & Index | Search, tag listing, index rebuild commands | ✅ Complete |
| **4** | Retrieval Quality | PDF ingestion, configurable providers | ✅ Complete |
| **5** | Observability | Logging, costs, quality dashboard | Future |
| **6** | Agent Integration | MCP tools, Open WebUI, workflows | Future |

---

## Cycle 1: Tracer Bullet ✅ Complete

**Goal**: Prove the architecture works end-to-end with minimal scope.

| Component | Delivered |
|-----------|-----------|
| Ingestion | YouTube transcripts (3 providers with fallback) |
| CLI | `kai ingest <url>` |
| Search | Tag + keyword + date (Whoosh) |
| Eval | 5-note baseline (14.8/15 avg) |
| Governance | Provenance metadata |
| **Robustness** | Circuit breaker, caching, provider fallback |

---

## Cycle 2: Multi-Source Ingestion ✅ Complete

**Goal**: Expand `kai ingest` to handle 3 source types with unified architecture.

### New Capabilities

| Source | Command |
|--------|---------|
| YouTube | `kai ingest https://youtube.com/...` |
| Web URL | `kai ingest https://article.com/...` |
| Markdown | `kai ingest ./path/to/file.md` |

### Robustness in Cycle 2

| Feature | Description |
|---------|-------------|
| Unified error handling | Consistent exceptions across providers |
| Input validation | Detect source type, validate URLs/paths |
| Graceful degradation | Clear error messages, partial success |
| Rate limiting | Basic rate limiter for web requests |
| Retry logic | Exponential backoff for transient failures |

### Observability in Cycle 2

| Feature | Description |
|---------|-------------|
| Structured logging | JSON logs with context (source, provider, duration) |
| Log levels | DEBUG for dev, INFO for production |
| Ingestion audit log | Append-only log of all ingestions with outcomes |
| `--verbose` flag | CLI option for detailed output |

### Week Plan

| Week | Focus |
|------|-------|
| 1 | Web URL ingestion + error handling + logging |
| 2 | Markdown ingestion + input validation |
| 3 | Provider abstraction + rate limiting + eval |

---

## Cycle 3: Inbox Organization ✅ Complete

**Goal:** Automatically organize notes from inbox into folders based on tags.

| Component | Delivered |
|-----------|-----------|
| Folder Rules | User-editable `folder_rules.json` for tag→folder mappings |
| Scoring Algorithm | Multi-tag scoring with specificity bonus for deeper paths |
| CLI Command | `kai process-inbox` with `--dry-run` mode |
| Batch Processing | Summary → confirmation → execute moves |
| Move Tracking | Append-only log in `.kai/folder_mappings.jsonl` |
| Error Handling | Path traversal protection, parse failure reporting |
| **Robustness** | Security validation, tag normalization, graceful failures |

### Features

| Feature | Description |
|---------|-------------|
| Rule-based classification | Match tags to folders via simple JSON config |
| Dry-run mode | Preview changes without executing moves |
| Batch confirmation | Human-in-the-loop validation before moving |
| Scoring system | Handles multiple matching tags intelligently |
| Move tracking | JSONL log for learning and audit trail |
| Error reporting | Warns about unparseable files |

### Robustness in Cycle 3

| Feature | Description |
|---------|-------------|
| Path security | Validates all folder paths to prevent traversal attacks |
| Tag normalization | Handles string/list/null tags from frontmatter |
| Early validation | Checks folder rules at load time, not during moves |
| Parse failure tracking | Reports files that couldn't be processed |
| Atomic helpers | DRY principle for consistent result construction |

---

## Cycle 4: Retrieval Quality ✅ Complete

**Goal**: Enhance content ingestion and retrieval capabilities.

| Feature | Status | Robustness |
|---------|--------|------------|
| PDF ingestion | ✅ Complete | Page limits, size validation, truncation warnings |
| Provider configuration | ✅ Complete | Configurable fallback order, CLI overrides |
| Semantic search | Deferred | Current Whoosh search sufficient for CLI use case |
| Podcast/audio | Backlog | Whisper integration for future consideration |

### PDF Ingestion ✅ Complete

| Component | Delivered |
|-----------|-----------|
| PDF Provider | pypdf-based text extraction |
| Local & Remote | Supports both file paths and URLs |
| Page Limiting | Default 50 pages, configurable via `--max-pages` |
| Supadata Fallback | Falls back to Supadata for protected URLs |
| Prompt Template | `pdf_v1` prompt based on `article_v1` |
| Error Handling | Graceful handling of empty, corrupted, encrypted PDFs |
| **Robustness** | Page/size limits, truncation warnings, clear error messages |

### CLI Commands

```bash
# Local PDF
kai ingest ./research/paper.pdf

# Remote PDF with custom page limit
kai ingest https://arxiv.org/pdf/2024.12345.pdf --max-pages 30

# Will fallback to Supadata if needed
kai ingest https://protected-site.com/document.pdf
```

### Provider Configuration ✅ Complete

| Component | Delivered |
|-----------|-----------|
| Environment Config | `YOUTUBE_TRANSCRIPT_PROVIDER_ORDER` setting |
| CLI Override | `--transcript-providers` parameter for runtime control |
| Provider Options | `direct` (free scraping), `supadata` (paid), `decodo` (paid) |
| Logging & Output | Shows provider order and which provider succeeded |
| **Robustness** | Validates provider names, graceful fallback, clear error messages |

### CLI Commands

```bash
# Use default order from .env (typically: direct,supadata,decodo)
kai ingest https://youtube.com/watch?v=...

# Force direct scraping only
kai ingest https://youtube.com/watch?v=... --transcript-providers direct

# Try Supadata first, fall back to direct
kai ingest https://youtube.com/watch?v=... --transcript-providers supadata,direct
```

Output shows which provider was used:
```
📥 Fetching content using youtube provider...
   🔍 Trying transcript providers: supadata, direct
   ✓ Transcript via supadata (23299 chars)
```

---

### Search Capabilities

Current Whoosh-based search provides:
- Full-text keyword search (BM25 ranking)
- Tag filtering
- Date range queries
- Combined queries with AND logic
- Highlighted snippets in results

**Decision**: Semantic search (embeddings + vectors) deferred to backlog. Current lexical search is sufficient for CLI-based personal knowledge management without requiring additional infrastructure.

| Feature | Robustness |
|---------|------------|
| Query logging | Log rotation |
| Cost attribution | Metrics resilience |
| Quality dashboard | Data durability |

---

## Cycle 5: Agent Integration (Future)

| Feature | Robustness |
|---------|------------|
| MCP tools | Timeout handling |
| Multi-step workflows | Partial failure recovery |
| Human-in-loop | Approval gates |

---

## Robustness: Cross-Cutting Concerns

Built incrementally, not deferred:

| Cycle | Robustness Focus |
|-------|------------------|
| 1 ✅ | Circuit breaker, caching, fallback |
| 2 ✅ | Error handling, validation, rate limiting, retries |
| 3 ✅ | Path security, tag normalization, parse failure tracking |
| 3.5 ✅ | Recursive scanning, cache invalidation, index corruption recovery |
| 4 ✅ | Provider configuration, page/size limits, graceful degradation |
| 5 | Log resilience, metrics durability |
| 6 | Timeouts, partial failures, oversight |

---

## Cycle 3.5: Vault Search & Index Management ✅ Complete

**Goal**: Enable searching and index management for the Obsidian vault.

| Component | Delivered |
|-----------|-----------|
| Search | Full-text keyword search via Whoosh |
| Tag Search | Tag-based filtering in search |
| Date Filtering | Created date range queries |
| Tag Listing | `kai list-tags` command with counts |
| Index Rebuild | `kai rebuild-index` command for full vault re-indexing |
| **Robustness** | Recursive vault scanning, cache invalidation, index corruption recovery |

### CLI Commands

| Command | Description |
|---------|-------------|
| `kai search --keyword "text"` | Full-text keyword search across all notes |
| `kai search --tag ai` | Filter notes by tag |
| `kai search --after 2026-01-01` | Filter by creation date |
| `kai list-tags` | Show all tags with note counts |
| `kai rebuild-index` | Force rebuild of vault and search indexes |

### Key Features

- **Whoosh Integration**: Full-text search index at `.kai/whoosh_index/`
- **Vault Metadata Cache**: JSON index at `.kai/vault_index.json`
- **Recursive Scanning**: `rebuild-index` scans entire vault, not just inbox
- **Cache Invalidation**: Automatic detection of file changes for incremental updates
- **Index Recovery**: Rebuild command handles corrupted indexes

---

## Backlog

**Deferred from Cycle 4:**
- Semantic search (embeddings + vector storage)
- Podcast/audio ingestion (Whisper API)

**Future enhancements:**
- Tag taxonomy enforcement
- Non-English transcripts (Whisper)
- Batch ingestion
- Provider health dashboard

---

## Decision Log

[docs/decisions.md](file:///Users/larshansen/Documents/Dev/Coding/obsidian-ai-tools/docs/decisions.md)
