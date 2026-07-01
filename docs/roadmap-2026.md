# Obsidian AI Pipeline — 2026 Strategic Roadmap

## Executive Summary

**Current Status**: Cycles 1-4 complete (100%), Cycle 5 partially complete
**Test Coverage**: 52.9% (174 tests passing)
**Architecture**: Production-ready CLI with MCP-ready core functions
**Strategic Direction**: CLI-first for capture, future MCP for analysis workflows

---

## Current State Assessment (as of 2026-01-04)

### ✅ Completed Capabilities

| Feature | Status | Key Metrics |
|---------|--------|-------------|
| **Multi-Source Ingestion** | Production | YouTube, web, PDF, markdown with intelligent provider fallbacks |
| **Provider Infrastructure** | Production | Circuit breaker, caching, rate limiting, exponential backoff |
| **Search & Discovery** | Production | Whoosh full-text search, tag filtering, date ranges |
| **Inbox Organization** | Production | Rule-based folder classification with audit trail |
| **Observability** | Production | DuckDB cost tracking, quality metrics via CLI |
| **Test Suite** | Strong | 174 tests, 52.9% coverage, ruff/mypy/pytest passing |

### 📊 Implementation Metrics

- **Source Lines**: ~1,758 (excluding tests)
- **CLI Commands**: 5 commands (`ingest`, `search`, `rebuild-index`, `process-inbox`, `version`)
- **Provider Types**: 4 (YouTube, Web, PDF, File)
- **Quality Gates**: All passing (ruff strict linting, mypy type checking, pytest)

### 🎯 Roadmap Status vs. Original Plan

| Cycle | Theme | Original Status | Current Status |
|-------|-------|-----------------|----------------|
| 1 | Tracer Bullet | ✅ Complete | ✅ Complete |
| 2 | Multi-Source | ✅ Complete | ✅ Complete |
| 3 | Inbox Organization | ✅ Complete | ✅ Complete |
| 3.5 | Vault Search & Index | ✅ Complete | ✅ Complete |
| 4 | Retrieval Quality | ✅ Complete | ✅ Complete |
| 5 | Observability | 🔄 Future | ✅ Partial (metrics complete, dashboard deferred) |
| 6 | Agent Integration | 🔄 Future | 🔄 Future (MCP design documented, not built) |

---

## Strategic Direction: CLI-First Philosophy

### Why CLI is the Primary Interface

**Decision**: CLI has proven to be the optimal interface for knowledge capture workflows.

**Rationale**:
1. **Speed**: `kai ingest <url>` is faster than UI context-switching
2. **Flow State**: No browser/GUI overhead during research
3. **Composability**: Unix pipes, scripting, OS automation (Alfred, Raycast, Shortcuts)
4. **Reliability**: Direct execution, no server dependencies

### MCP's Complementary Role

**MCP is for analysis, not capture.**

When to build MCP tools:
- **Cross-tool workflows**: Search vault while conversing with Claude
- **Synthesis operations**: "Summarize all notes tagged #ai"
- **Connection discovery**: "Find related concepts to this draft"
- **Multi-note analysis**: Generate outlines from multiple sources

**Not for**: Basic ingestion (CLI is superior)

---

## Cycle 6: Knowledge Utilization (Q1 2026)

**Goal**: Transform captured knowledge into actionable insights through review loops and connection discovery.

### 6.2: Inbox Triage Preview 🎯 High Priority

**Problem**: Not all URLs warrant full ingestion; need quick preview to decide

**Solution**: `kai preview` command

```bash
# Preview URL without full ingestion
kai preview https://youtube.com/watch?v=...

→ Preview: "GPT-4 Vision API Tutorial"
  Source: YouTube video (23:15)
  Transcript length: ~8,500 words
  Estimated cost: $0.04
  Key topics: API usage, image encoding, prompt engineering

  Actions:
    [i] Ingest now
    [x] Skip

# Batch preview from clipboard
pbpaste | kai preview --batch
```

**Technical Approach**:
- Reuse provider infrastructure (fetch metadata only, no LLM call)
- Cost estimation from transcript/content length
- Topic extraction via simple keyword analysis (no LLM needed)

**Deliverables**:
- [x] `preview.py` module with metadata extraction
- [x] `kai preview` CLI command with interactive mode
- [x] Batch mode for clipboard URLs
- [x] Tests for preview generation

> [!NOTE] 
> **Completed Jan 2026**: Added the `preview` command.

**Effort**: Low (2-3 days)
**Value**: Medium-High (reduces noise, intentional vault building)

---

## Cycle 7: MCP Integration for Analysis (Q2 2026)

**Goal**: Enable cross-tool workflows where LLM agents can query and synthesize vault knowledge.

### 7.1: MCP Server Foundation

**MCP Tools to Build** (not for ingestion):

1. **`search_vault`**: Full-text search with filters
   ```python
   @mcp_tool()
   def search_vault(query: str, tags: list[str], limit: int) -> list[dict]:
       """Search vault and return matching notes"""
   ```

2. **`get_note_content`**: Retrieve full note by path
   ```python
   @mcp_tool()
   def get_note_content(note_path: str) -> dict:
       """Fetch note content and metadata"""
   ```

3. **`find_related_notes`**: Concept linking integration
   ```python
   @mcp_tool()
   def find_related_notes(note_path: str, limit: int) -> list[dict]:
       """Find conceptually related notes"""
   ```

4. **`synthesize_notes`**: Multi-note summarization
   ```python
   @mcp_tool()
   def synthesize_notes(note_paths: list[str], focus: str) -> str:
       """Generate synthesis across multiple notes"""
   ```

**Architecture**:
- Reuse existing pure functions (zero code duplication)
- MCP server in `mcp/server.py`
- Timeout handling (30s default)
- Partial failure recovery
- Human-in-the-loop confirmation for write operations

**Deliverables**:
- [ ] MCP server setup with Claude SDK
- [ ] 4 core search/retrieval tools
- [ ] Integration tests with Open WebUI
- [ ] Documentation for MCP setup

**Effort**: Medium (5-7 days)
**Value**: Medium-High (enables new workflows, complements CLI)

---

### 7.2: Advanced MCP Workflows

**Future enhancements** (post Q2):
- Multi-step research workflows
- Automatic tag suggestions during conversations
- Draft enhancement based on vault knowledge
- Connection suggestion during writing

---

## Migration to Hexagonal Architecture (Deferred)

**Current Decision**: Maintain pragmatic flat-function design

**When to reconsider**:
1. Team grows beyond 2 developers
2. Need to support 5+ source types with complex shared logic
3. Testing becomes difficult with current structure
4. Shared use cases across CLI + MCP + web UI

**Migration path documented** in `ARCHITECTURE.md` (Phases 1-5)

**Estimated effort if needed**: 1-2 weeks
**Code reuse**: 70-80% of current functions

---

## Prioritized Feature Roadmap (Next 3 Months)

| Week | Feature | Effort | Value | Status |
|------|---------|--------|-------|--------|
| **Week 2-3** | Inbox Triage Preview | Low | High | ✅ Complete |
| **Week 10-11** | Backlink-Boosted BM25 | Low | High | ✅ Complete |
| **Week 11-13** | MCP Server Foundation | Medium | Medium-High | 🔄 Planned |

---

## Backlog (Deferred Indefinitely)

**From previous roadmap:**
- Semantic search (embeddings + vectors) — **removed**: Annoy and sentence-transformers dependencies dropped in favour of BM25F + backlink boost, which outperforms hybrid search on personal vault benchmarks
- Podcast/audio ingestion (Whisper) - not prioritized
- Tag taxonomy enforcement - manual management works fine
- Observability dashboard UI - CLI output adequate

**New backlog items:**
- Topic drift detection / taxonomy analysis
- Batch ingestion from multiple URLs
- Provider health dashboard
- Multi-language transcript support

---

## Success Metrics

**Cycle 6 (Knowledge Utilization):**
- [ ] Preview command reduces low-quality ingestion by 20%+

**Cycle 7 (MCP Integration):**
- [ ] MCP server successfully integrated with Open WebUI
- [ ] Search vault tool used in 5+ conversations per week
- [ ] Synthesis operations generate useful cross-note insights

---

## Decision Log

Key architectural and strategic decisions:

1. **CLI-first for capture** (2026-01-04): CLI proven superior to UI for ingestion workflows
2. **MCP for analysis only** (2026-01-04): MCP complements CLI, doesn't replace it
3. **Remove semantic search** (2026-03): Annoy + sentence-transformers removed; BM25F + backlink boost sufficient and faster to install
4. **Maintain flat-function design** (ongoing): No hexagonal migration needed yet
5. **Observability via CLI** (2025): Dashboard UI deferred, CLI output adequate

See `decisions.md` for historical context.

---

## Robustness: Cross-Cutting Concerns

Robustness continues to be built incrementally:

| Cycle | Robustness Focus |
|-------|------------------|
| 1-4 ✅ | Circuit breaker, caching, fallback, rate limiting, path security |
| 5 ✅ | Cost tracking, quality metrics, error resilience |
| 6 🔄 | Preview cost estimation |
| 7 🔄 | MCP timeouts, partial failure recovery, confirmation gates |

---

## Summary

**Where we are**: Production-ready CLI with excellent test coverage and robust infrastructure

**Where we're going**:
- **Short term** (Q1): Knowledge utilization (digests, previews, connections)
- **Medium term** (Q2): MCP for analysis workflows (search, synthesis)
- **Long term**: Continuous refinement based on usage patterns

**Key insight**: The CLI has proven its value for capture workflows. Future work focuses on **making captured knowledge more useful** through review loops, connections, and cross-tool integration.

The architecture remains intentionally simple and MCP-ready, allowing incremental enhancement without refactoring.
