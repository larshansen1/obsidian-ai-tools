# Architecture Documentation

## Overview

This document describes the current pragmatic architecture and provides a clear path for migrating to an enterprise-grade hexagonal architecture when needed.

## Current Architecture (Pragmatic - Week 1 MVP)

### Design Philosophy

The current architecture prioritizes:
- **Speed to ship**: Minimal abstraction layers
- **Pure functions**: Core logic is MCP-ready
- **Clear modules**: Each module has a single responsibility
- **Quality gates**: All code passes ruff, mypy, pytest
- **No over-engineering**: YAGNI principle applied rigorously

### Module Structure

```
src/obsidian_ai_tools/
├── __init__.py           # Package exports
├── __main__.py           # Entry point for python -m
├── config.py             # Pydantic settings (env loading)
├── models.py             # Data classes (VideoMetadata, Note)
├── youtube.py            # Pure functions: fetch transcripts
├── llm.py                # Pure functions: generate notes via OpenRouter
├── obsidian.py           # Pure functions: write to vault
├── indexer.py            # Vault scanning (NoteMetadata, VaultIndex, build_index)
├── search.py             # BM25F search + backlink boost (Whoosh)
├── wikilinks.py          # Shared wikilink utilities (extract, resolve, backlinks)
├── overview.py           # Vault terrain map (per-folder TF-IDF keywords + tags)
├── concept_linking.py    # TF-IDF similarity for kai connect
├── folder_organizer.py   # Rule-based inbox organisation
├── digest.py             # Vault activity digest generation
├── preview.py            # URL preview without full ingestion
├── refresh.py            # Re-process notes with updated prompts
├── tag_hygiene.py        # Tag deduplication and consolidation
└── cli.py                # Typer CLI (thin orchestration layer)

prompts/
├── youtube_v1.md         # YouTube transcript prompt
├── youtube_v2.md
├── article_v1.md
├── pdf_v1.md
└── markdown_v1.md
```

### Data Flow

```
User: kai ingest <youtube-url>
  ↓
cli.py: ingest() command
  ↓
config.py: load Settings from .env
  ↓
youtube.py: get_video_metadata(url) → VideoMetadata
  ↓
llm.py: generate_note(metadata, ...) → Note
  ↓
obsidian.py: write_note(note, ...) → Path
  ↓
Success message
```

### Key Design Decisions

1. **Flat function-based modules** instead of classes
   - Easier to test
   - Lower cognitive overhead
   - Pure functions are MCP-ready

2. **Single source of truth** for configuration (pydantic-settings)
   - Type-safe validation
   - Environment variable support
   - No boilerplate

3. **Explicit error types** for each module
   - `youtube.py`: `InvalidYouTubeURLError`, `TranscriptUnavailableError`
   - `llm.py`: `NoteGenerationError`, `PromptTemplateError`
   - `obsidian.py`: `FileWriteError`, `PathTraversalError`

4. **Prompt templates as files** (not hardcoded strings)
   - Version control
   - Easy iteration
   - Clear separation of code and prompts

### MCP Conversion (Future)

The current pure functions can be wrapped for MCP with zero changes:

```python
# Current CLI (cli.py)
def ingest(url: str):
    metadata = get_video_metadata(url)
    note = generate_note(metadata, ...)
    write_note(note, ...)

# Future MCP server (mcp/server.py)
@mcp_tool()
def ingest_youtube(url: str) -> dict:
    metadata = get_video_metadata(url)  # Same function!
    note = generate_note(metadata, ...)   # Same function!
    path = write_note(note, ...)          # Same function!
    return {"status": "success", "path": str(path)}
```

---

## Enterprise Migration Path

### When to Migrate

Consider migrating to hexagonal architecture when you encounter:

1. **Multiple source types** (articles, PDFs, podcasts) with similar workflows
2. **Team growth** (3+ developers) requiring clearer boundaries
3. **Complex workflows** (multi-step ingestion, human-in-the-loop approval)
4. **Testing challenges** (need more granular mocking)
5. **Shared logic** across CLI and MCP server

### Hexagonal Architecture Overview

```
┌─────────────────────────────────────────────┐
│            Application Core                 │
│   ┌─────────────────────────────────┐      │
│   │     Domain Layer                 │      │
│   │  (entities, value objects,       │      │
│   │   pure business logic)           │      │
│   └─────────────────────────────────┘      │
│                  ↑                          │
│   ┌─────────────────────────────────┐      │
│   │     Ports (Interfaces)           │      │
│   │  - TranscriptFetcher             │      │
│   │  - LLMClient                     │      │
│   │  - NoteWriter                    │      │
│   └─────────────────────────────────┘      │
│                  ↑                          │
│   ┌─────────────────────────────────┐      │
│   │  Application Layer (Use Cases)   │      │
│   │  IngestContentUseCase            │      │
│   └─────────────────────────────────┘      │
└─────────────────────────────────────────────┘
                   ↑
        ┌──────────┴──────────┐
        │                     │
┌───────────────┐   ┌──────────────────┐
│   Adapters    │   │    Adapters      │
│               │   │                  │
│ - CLI         │   │ - MCP Server     │
│ - YouTube API │   │ - OpenRouter API │
│ - Filesystem  │   │ - Article API    │
└───────────────┘   └──────────────────┘
```

### Migration Steps (Incremental)

#### Phase 1: Extract Domain Entities (Low Risk)

**Current:**
```python
# models.py
class VideoMetadata(BaseModel): ...
class Note(BaseModel): ...
```

**Hexagonal:**
```python
# domain/entities.py
@dataclass(frozen=True)
class Content:  # More generic than VideoMetadata
    source_url: str
    title: str
    body: str
    source_type: str

@dataclass(frozen=True)
class Note:  # Same concept, immutable
    title: str
    content: str
    tags: list[str]
```

**Migration steps:**
1. Create `src/obsidian_ai_tools/domain/entities.py`
2. Copy current models, make them immutable dataclasses
3. Update imports gradually
4. Remove old `models.py`

---

#### Phase 2: Define Ports (Interfaces)

**Current:**
```python
# youtube.py
def get_video_metadata(url: str) -> VideoMetadata: ...

# llm.py
def generate_note(metadata, model, api_key) -> Note: ...
```

**Hexagonal:**
```python
# ports/content_fetcher.py
from typing import Protocol

class ContentFetcher(Protocol):
    def fetch(self, url: str) -> Content:
        """Fetch content from URL."""
        ...

# ports/note_generator.py
class NoteGenerator(Protocol):
    def generate(self, content: Content) -> Note:
        """Generate note from content."""
        ...
```

**Migration steps:**
1. Create `src/obsidian_ai_tools/ports/` directory
2. Define Protocol classes for each integration point
3. Current functions become adapter implementations
4. No breaking changes yet (Protocols are duck-typed)

---

#### Phase 3: Create Adapters

**Current:**
```python
# youtube.py (functions)
def get_video_metadata(url): ...
```

**Hexagonal:**
```python
# adapters/youtube_fetcher.py
class YouTubeFetcher:
    """Adapter implementing ContentFetcher for YouTube."""

    def fetch(self, url: str) -> Content:
        video_id = extract_video_id(url)
        transcript = fetch_transcript(video_id)
        # Transform YouTube-specific data → domain Content
        return Content(...)
```

**Migration steps:**
1. Create `src/obsidian_ai_tools/adapters/` directory
2. Wrap existing functions in classes
3. Keep pure function implementations (extract as helpers)
4. Adapters handle external API calls, pure functions handle logic

---

#### Phase 4: Add Use Cases (Application Layer)

**Current:**
```python
# cli.py
def ingest(url: str):
    metadata = get_video_metadata(url)
    note = generate_note(metadata, ...)
    write_note(note, ...)
```

**Hexagonal:**
```python
# application/ingest_content.py
class IngestContentUseCase:
    def __init__(
        self,
        fetcher: ContentFetcher,
        generator: NoteGenerator,
        writer: NoteWriter,
    ):
        self._fetcher = fetcher
        self._generator = generator
        self._writer = writer

    def execute(self, url: str) -> Path:
        content = self._fetcher.fetch(url)
        note = self._generator.generate(content)
        return self._writer.write(note)
```

**Migration steps:**
1. Create `src/obsidian_ai_tools/application/` directory
2. Extract orchestration logic from CLI into use cases
3. Use cases depend on ports (not concrete implementations)
4. CLI becomes thin adapter calling use cases

---

#### Phase 5: Dependency Injection

**Current:**
```python
# cli.py
settings = get_settings()
note = generate_note(metadata, settings.llm_model, settings.api_key)
```

**Hexagonal:**
```python
# infrastructure/di_container.py
def build_ingest_use_case(settings: Settings) -> IngestContentUseCase:
    fetcher = YouTubeFetcher()
    generator = OpenRouterGenerator(
        api_key=settings.openrouter_api_key,
        model=settings.llm_model,
    )
    writer = ObsidianWriter(vault_path=settings.vault_path)

    return IngestContentUseCase(
        fetcher=fetcher,
        generator=generator,
        writer=writer,
    )

# cli.py
@app.command()
def ingest(url: str):
    use_case = build_ingest_use_case(get_settings())
    path = use_case.execute(url)
```

**Migration steps:**
1. Create `src/obsidian_ai_tools/infrastructure/di_container.py`
2. Define factory functions for each use case
3. Update CLI to call factories
4. Update tests to inject mocks

---

### Migration Comparison

| Aspect | Current (Pragmatic) | Hexagonal (Enterprise) |
|--------|---------------------|------------------------|
| **Files** | ~16 modules | 15-20 files across layers |
| **Complexity** | Low | Medium |
| **Testability** | Good (pure functions) | Excellent (ports enable easy mocking) |
| **MCP conversion** | Manual wrapping | Swap CLI adapter for MCP adapter |
| **Multi-source** | Add functions to modules | Implement new adapter |
| **Team size** | 1-2 developers | 3+ developers |
| **Time to implement** | 3-4 hours | 1-2 days |

---

### Code Reuse During Migration

The good news: **pure functions stay pure!**

```python
# Current youtube.py (stays the same)
def extract_video_id(url: str) -> str: ...
def fetch_transcript(video_id: str) -> str: ...

# Hexagonal wrapper (new)
class YouTubeFetcher:
    def fetch(self, url: str) -> Content:
        video_id = extract_video_id(url)  # Reuse!
        transcript = fetch_transcript(video_id)  # Reuse!
        return Content(...)  # New: transform to domain model
```

**Estimated reuse: 70-80% of current code**

---

## Testing Strategy

### Current (MVP)

- **Unit tests**: Pure functions (youtube, llm, obsidian helpers)
- **Enforced floor**: 80% (`fail_under = 80` in `pyproject.toml`)
- **Production target**: 85%+

### Enterprise

- **Domain layer**: 100% coverage (pure logic, no I/O)
- **Use cases**: Integration tests with mocked ports
- **Adapters**: Contract tests ensuring port compliance
- **E2E**: Full workflow tests

---

## Summary

### Current State
✅ Working CLI tool (16 commands)
✅ Pure functions (MCP-ready)
✅ Quality gates passing
✅ BM25F search with backlink boosting
✅ Vault terrain map (kai overview)
✅ Wikilink traversal (kai follow)

### Future State (When Needed)
🎯 Hexagonal architecture
🎯 Clear adapter boundaries
🎯 Easy multi-source support
🎯 Shared use cases across CLI/MCP
🎯 Enterprise-grade testing

### Key Takeaway

**Start pragmatic, migrate incrementally.** The current architecture is designed to evolve, not be thrown away. Each migration phase is independent and low-risk.
