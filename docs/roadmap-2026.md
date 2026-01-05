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
- **CLI Commands**: 8 commands (`ingest`, `search`, `list-tags`, `rebuild-index`, `process-inbox`, `stats`, `quality`, `version`)
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

### 6.1: Daily/Weekly Digests 🎯 High Priority

**Problem**: Notes accumulate without review; patterns go unnoticed

**Solution**: `kai digest` command

```bash
# Generate weekly summary
kai digest --days 7 --output ~/Desktop/weekly-review.md

# Example output
→ Weekly Knowledge Digest (Dec 28 - Jan 4)
  📊 9 new notes ingested (3 YouTube, 4 web, 2 PDFs)
  🏷️  Top tags: #ai (4), #programming (3), #productivity (2)
  🔗 Most referenced: [[Attention Mechanisms]] (3 links)
  💡 Suggested actions:
    - Create folder for "productivity" notes (3+ unorganized)
    - Review "AI/LLMs" folder (growing fast)
```

**Technical Approach**:
- Pure function: `generate_digest(since_days: int, vault_path: Path) -> DigestReport`
- Leverages existing indexer and search infrastructure
- Output formats: Markdown, JSON, terminal-friendly
- Integration: cron job for Monday morning automation

**Deliverables**:
- [x] `digest.py` module with digest generation logic
- [x] `kai digest` CLI command with `--days`, `--output`, `--format` options
- [x] Pydantic `DigestReport` model with metrics
- [x] Tests for digest generation and formatting
- [x] Documentation and example cron setup

**Effort**: Low (2-3 days)
**Value**: High (builds review habit, surfaces patterns)

---

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
    [s] Save to reading list
    [x] Skip

# Batch preview from clipboard
pbpaste | kai preview --batch
```

**Technical Approach**:
- Reuse provider infrastructure (fetch metadata only, no LLM call)
- Cost estimation from transcript/content length
- Topic extraction via simple keyword analysis (no LLM needed)
- Reading list stored in `.kai/reading_list.jsonl`

**Deliverables**:
- [x] `preview.py` module with metadata extraction
- [x] `kai preview` CLI command with interactive mode
- [x] Reading list persistence and management (`kai reading-list` subcommands)
- [x] Batch mode for clipboard URLs
- [x] Tests for preview generation

> [!NOTE] 
> **Completed Jan 2026**: Added full `kai reading-list` management (list, ingest, clear) and `preview` command.

**Effort**: Low (2-3 days)
**Value**: Medium-High (reduces noise, intentional vault building)

---

### 6.3: Concept Linking & Connection Discovery 🎯 Medium Priority

**Problem**: Notes exist in isolation; manual linking is tedious

**Solution**: `kai connect` command

```bash
# Find related notes for a specific file
kai connect --note "AI/LLMs/Attention Mechanisms.md"

→ Found 3 potential connections:
  - AI/Transformers/Self-Attention.md (similarity: 0.87)
  - AI/Neural Nets/Backprop.md (mentions: attention gradient)
  - Reading/Deep Learning Book.md (chapter 10)

# Batch-generate suggestions for orphan notes
kai connect --orphans --dry-run

# Auto-insert links (confirmation required)
kai connect --note "AI/LLMs/Attention.md" --auto-link --confirm
```

**Technical Approach**:
- **Phase 1**: TF-IDF similarity (no embeddings needed)
- **Phase 2**: Keyword co-occurrence analysis
- **Phase 3** (optional): Sentence embeddings if needed
- Respect existing `[[wikilinks]]` in Obsidian format
- Dry-run mode for preview before modification

**Deliverables**:
- [x] `concept_linking.py` module with similarity algorithms
- [x] `kai connect` CLI command with folder scanning & auto-link
- [x] Wikilink insertion with confirmation flow (sanitized & aliased)
- [x] Orphan note detection
- [x] Tests for similarity scoring and link insertion

> [!NOTE]
> **Completed Jan 2026**: Implemented `kai connect` with TF-IDF. Added folder scanning mode (`--folder`), aliased wikilinks (`[[path|Title]]`), and configurable similarity thresholds.

**Effort**: Medium (5-7 days)
**Value**: High (builds knowledge graph organically, surfaces forgotten notes)

---

### 6.4: Smart Re-Processing 🎯 Medium Priority

**Problem**: Prompt templates improve, but old notes use outdated prompts

**Solution**: `kai refresh` command

```bash
# Re-run LLM on old notes with new prompt version
kai refresh --tag ai --prompt-version youtube_v2 --since 30d --dry-run

→ Found 12 notes eligible for refresh:
  - AI/Attention.md (v1 → v2)
  - AI/Transformers.md (v1 → v2)

# Preview changes before applying
kai refresh --show-diff "AI/Attention.md" --prompt-version youtube_v2

# Execute refresh (creates backups)
kai refresh --tag ai --prompt-version youtube_v2 --confirm
```

**Technical Approach**:
- Detect prompt version from note frontmatter
- Fetch original source (URL stored in metadata)
- Re-generate note with new prompt
- Safety: Create `Note_v2.md` or use git branches (never overwrite)
- Cost tracking for re-processing operations

**Deliverables**:
- [ ] `refresh.py` module with re-processing logic
- [ ] `kai refresh` CLI command with filtering options
- [ ] Version tracking in frontmatter
- [ ] Backup/versioning strategy (git or `_v2` suffix)
- [ ] Cost estimation and confirmation prompts
- [ ] Tests for version detection and re-generation

**Effort**: Low-Medium (3-5 days)
**Value**: Medium (continuous improvement without manual effort)

---

### 6.5: Flashcard Extraction (Optional) 🔄 Low Priority

**Problem**: Notes are passive; insights get buried

**Solution**: `kai extract-flashcards` command

```bash
# Extract Q&A pairs from a note
kai extract-flashcards "AI/LLMs/Attention.md" --output-format anki

→ Generated 7 flashcards:
  Q: What problem does self-attention solve in sequence models?
  A: Captures long-range dependencies without RNN bottleneck

# Batch mode for recent notes
kai extract-flashcards --since 7d --tag ai --output ~/flashcards/
```

**Technical Approach**:
- New LLM prompt template: `flashcard_extraction_v1.md`
- Output formats: Anki deck, CSV, Markdown
- Integration with Anki via AnkiConnect API (optional)
- Append to dedicated `Flashcards/` folder in vault

**Deliverables**:
- [ ] `flashcard_extraction.py` module
- [ ] Flashcard extraction prompt template
- [ ] `kai extract-flashcards` CLI command
- [ ] Multiple output format support
- [ ] Tests for extraction and formatting

**Effort**: Medium (4-6 days)
**Value**: Medium (only if user practices spaced repetition)

---

### 6.6: Topic Drift Detection (Backlog) 🔄 Future

**Problem**: Tags/folders become dumping grounds over time

**Solution**: `kai analyze-taxonomy` command

```bash
# Detect overly broad tags
kai analyze-taxonomy --suggest-splits

→ Tag "AI" has 47 notes with 3 distinct clusters:
  Cluster 1 (23 notes): LLM architecture, transformers
    Suggest: Create "AI/LLMs" subtag
  Cluster 2 (18 notes): Computer vision, CNNs
    Suggest: Create "AI/Vision" subtag
```

**Technical Approach**:
- Keyword clustering (scikit-learn KMeans)
- Works with existing tag data (no embeddings)
- Suggests folder rule updates
- Preview mode before applying changes

**Deliverables**: TBD (backlog)

**Effort**: High (7-10 days, requires tuning)
**Value**: Low-Medium (manual taxonomy management works fine at current scale)

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
| **Week 1-2** | Daily/Weekly Digest | Low | High | 🔄 Planned |
| **Week 2-3** | Inbox Triage Preview | Low | High | 🔄 Planned |
| **Week 4-5** | Smart Re-Processing | Low-Med | Medium | 🔄 Planned |
| **Week 6-8** | Concept Linking | Medium | High | 🔄 Planned |
| **Week 9-10** | Flashcard Extraction | Medium | Medium | ⏸️ Optional |
| **Week 11-13** | MCP Server Foundation | Medium | Medium-High | 🔄 Planned |

---

## Backlog (Deferred Indefinitely)

**From previous roadmap:**
- Semantic search (embeddings + vectors) - current lexical search sufficient
- Podcast/audio ingestion (Whisper) - not prioritized
- Tag taxonomy enforcement - manual management works fine
- Observability dashboard UI - CLI stats output adequate

**New backlog items:**
- Topic drift detection / taxonomy analysis
- Batch ingestion from multiple URLs
- Provider health dashboard
- Multi-language transcript support

---

## Success Metrics

**Cycle 6 (Knowledge Utilization):**
- [ ] Weekly digest automation running via cron
- [ ] Preview command reduces low-quality ingestion by 20%+
- [ ] Concept linking generates 10+ useful connections per week
- [ ] Smart re-processing improves 50+ old notes with new prompts

**Cycle 7 (MCP Integration):**
- [ ] MCP server successfully integrated with Open WebUI
- [ ] Search vault tool used in 5+ conversations per week
- [ ] Synthesis operations generate useful cross-note insights

---

## Decision Log

Key architectural and strategic decisions:

1. **CLI-first for capture** (2026-01-04): CLI proven superior to UI for ingestion workflows
2. **MCP for analysis only** (2026-01-04): MCP complements CLI, doesn't replace it
3. **Defer semantic search** (2025): Whoosh lexical search sufficient for personal use
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
| 6 🔄 | Preview cost estimation, link validation, refresh safety |
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
