# Architecture

`obsidian-ai-tools` is a personal knowledge ingestion tool. It fetches content from URLs or files, generates structured Obsidian notes via an LLM, and writes them to a vault. It has three entry points: a CLI (`kai`), a local HTTP server consumed by a Chrome extension, and a Python API (`ingest_content()`).

---

## Module map

```
src/obsidian_ai_tools/
│
├── cli.py                    # Typer app + command registration (30 lines — pure router)
├── __main__.py               # python -m entry point
│
├── commands/                 # One module per command group
│   ├── ingest.py             # kai ingest
│   ├── preview.py            # kai preview
│   ├── search.py             # kai search
│   ├── serve.py              # kai serve, kai version
│   └── vault.py              # kai rebuild-index, process-inbox, usage
│
├── providers/                # Content fetching, one provider per source type
│   ├── base.py               # BaseProvider (abstract: name, validate, _ingest)
│   ├── factory.py            # ProviderFactory — selects provider by URL/path
│   ├── web.py                # WebProvider: Trafilatura → Supadata fallback
│   ├── pdf.py                # PDFProvider: local pypdf → remote download → Supadata fallback
│   ├── youtube.py            # YouTubeProvider: delegates to YouTubeClient
│   └── file.py               # FileProvider: local markdown/text files
│
├── server/
│   └── app.py                # FastAPI app: GET /status, POST /ingest
│                             #   consumed by Chrome extension via kai serve
│
├── utils/
│   └── rate_limiter.py       # Shared rate limiter (delay between HTTP requests)
│
├── ingestion.py              # Central orchestration: ProviderFactory → LLM → vault write
├── llm.py                    # generate_note(metadata, existing_tags) → (Note, CostInfo)
├── obsidian.py               # write_note(), parse_frontmatter()
├── observability.py          # DuckDB storage + get_db() singleton + @track_command
├── config.py                 # Pydantic Settings, get_settings() (lru_cached)
├── models.py                 # VideoMetadata, ArticleMetadata, Note, CostInfo, …
├── indexer.py                # VaultIndex, build_index() — scans vault markdown
├── search.py                 # BM25F search via Whoosh + backlink boosting
├── wikilinks.py              # extract_wikilinks(), resolve_wikilink(), count_backlinks()
├── preview.py                # URL preview without full ingestion
├── folder_organizer.py       # Rule-based inbox routing for kai process-inbox
├── youtube.py                # YouTubeClient — transcript fetching coordination
├── youtube_providers.py      # Transcript providers: direct, Supadata, Decodo
├── youtube_exceptions.py     # InvalidYouTubeURLError, TranscriptUnavailableError
├── transcript_validation.py  # Sanity checks on raw transcript data
├── cache.py                  # File-backed cache for provider responses
├── circuit_breaker.py        # Circuit breaker for external service calls
├── api_contracts.py          # Pydantic schemas for external API responses
│                             #   ⚠ currently 0 callers — see issue #32
└── logging.py                # Structured logging setup

prompts/                      # Versioned LLM prompt templates (Markdown)
    youtube_v1.md, youtube_v2.md, article_v1.md, pdf_v1.md,
    markdown_v1.md, flashcard_v1.md
```

---

## Entry points

| Entry point | How | Lands in |
|-------------|-----|----------|
| `kai <command>` | CLI (Typer) | `commands/<module>.py` |
| `POST /ingest` | HTTP (Chrome extension → `kai serve`) | `server/app.py` |
| `ingest_content(request, settings)` | Python API | `ingestion.py` directly |

All three converge on `ingestion.py: ingest_content()` for the actual work.

---

## Ingestion pipeline

```
Entry point
  (CLI command / HTTP POST / Python call)
        │
        ▼
  ingestion.py: ingest_content()
        │
        ├─ ProviderFactory.get_provider(url)
        │         │
        │         ▼
        │   providers/{web,pdf,youtube,file}.py
        │   → primary attempt
        │   → fallback attempt (web: Supadata, pdf: Supadata)
        │   → record_provider_attempt() ──────────────────────┐
        │                                                      │
        ├─ llm.py: generate_note(metadata, existing_tags)      │
        │         → (Note, CostInfo)                          │
        │                                                      │
        ├─ ingestion.py: record_cost() ──────────────────────┤
        │                                                      │
        ├─ obsidian.py: write_note()                          │
        │                                                      ▼
        └─ @track_command / serve:ingest ──────► observability.py (DuckDB)
```

`generate_note()` is pure: it accepts `existing_tags` as a parameter and returns `(Note, CostInfo)` with no I/O side-effects. The caller (`ingestion.py`) is responsible for tag discovery and cost recording.

---

## Observability

All writes are best-effort — a DB failure never blocks the operation. The singleton `get_db()` in `observability.py` lazily initialises one `ObservabilityDB` instance per process.

**DuckDB file:** `{vault_path}/.kai/observability.duckdb`

| Table | What it records |
|-------|----------------|
| `costs` | LLM token usage and USD cost per ingest |
| `metrics` | Ingestion outcome (success/failure) per source type |
| `command_invocations` | Every CLI command or `serve:ingest` call: outcome, duration |
| `provider_attempts` | Every provider attempt: primary vs fallback, outcome, duration |

**`@track_command(name)`** — decorator on all user-facing CLI functions. Records outcome (`success` / `error` / `user_abort`) and wall-clock duration in a swallowed `finally` block.

**`kai usage [--days N] [--all]`** — surfaces `command_invocations` and `provider_attempts` as a terminal report.

---

## Key design decisions

**Single config source.** `config.py: get_settings()` is `@lru_cache`; all modules import it. Settings are validated by Pydantic at startup with explicit error messages.

**Explicit error types.** Each module raises its own typed exceptions (`ContentFetchError`, `NoteGenerationStageError`, `VaultWriteError`, …). The CLI catches these and maps them to user-facing messages; the HTTP server maps them to HTTP status codes.

**Prompt templates as versioned files.** `prompts/*.md` are loaded at runtime. Iterating on note quality is a file edit, not a code change.

**Providers are pluggable via the factory.** `ProviderFactory.get_provider(source)` calls `validate()` on each registered provider in priority order. Adding a new source type means implementing `BaseProvider` and registering it — no changes to `ingestion.py` or the CLI.

**Observability never blocks.** Every `record_*()` call is wrapped in `try/except` that logs a warning and continues. The `@track_command` decorator's `finally` block swallows all errors.

---

## Testing conventions

- **Real DuckDB in `tmp_path`** for observability tests; never the production DB.
- **`_set_db_for_test(db)`** injects a per-test DB; the global autouse fixture in `conftest.py` ensures isolation automatically.
- **80% coverage floor** (`fail_under = 80`); branch coverage enabled.
- **Pre-commit gates:** ruff, bandit, gitleaks, radon complexity, readme reference check, pytest fast subset.
- **Pre-push gates:** mypy (strict), full pytest + coverage.

See `DEVELOPMENT.md` for the full quality gate reference and `CLI_COMMANDS.md` for command signatures.

---

## Open architecture work

| Issue | Topic |
|-------|-------|
| [#31](https://github.com/larshansen1/obsidian-ai-tools/issues/31) | Consolidate duplicate rate-limiter and fallback pattern across `web.py` / `pdf.py` |
| [#32](https://github.com/larshansen1/obsidian-ai-tools/issues/32) | Delete `api_contracts.py` (0 callers) or wire its validators at call sites |
| [#14](https://github.com/larshansen1/obsidian-ai-tools/issues/14) | Provider plugin/adapter structure — blocked on usage review [#29](https://github.com/larshansen1/obsidian-ai-tools/issues/29) |
| [#30](https://github.com/larshansen1/obsidian-ai-tools/issues/30) | This document update (closes on merge of this commit) |
