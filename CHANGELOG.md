# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-06-02

### Added

**Ingestion**
- `kai ingest` — generate structured Obsidian notes from YouTube videos, web articles, PDFs (local and remote), and local Markdown files
- Versioned prompt templates in `prompts/` — note quality can be improved without touching Python code
- YouTube transcript fallback chain: direct scraping → Supadata → Decodo
- `kai preview` — preview a URL and estimate ingestion cost before committing to the vault
- ChatGPT and Claude conversation ingestion via the Chrome extension

**Vault organisation**
- `kai process-inbox` — move inbox notes to folders by tag rules
- `kai update-rules` — suggest or auto-add missing folder rules based on unmatched tags
- `kai refresh` — regenerate older notes with a newer prompt version

**Search and exploration**
- `kai search` — BM25F full-text search with backlink boosting, tag and date filters, and relevance explanations
- `kai rebuild-index` — rebuild the Whoosh search index
- `kai list-tags` — list vault tags globally or by folder
- `kai overview` — per-folder keyword and tag distributions (compact mode for agent context)
- `kai follow` — resolve a note title and print its full content
- `kai digest` — summarise recent vault activity into a Markdown digest

**Linking and maintenance**
- `kai connect` — TF-IDF-based wikilink suggestions and auto-insertion
- `kai tags` — tag hygiene analysis with apply plan
- `kai flashcards` — AI-generated Obsidian Spaced Repetition flashcards from note content

**Reading list**
- `kai reading-list list / ingest / clear` — save URLs for later and batch-ingest them

**Observability**
- `kai stats` — LLM API cost statistics from DuckDB
- `kai quality` — ingestion success rates and common error patterns

**Local service**
- `kai serve` — FastAPI webhook server for browser-based ingestion (foreground and background modes)
- Chrome extension for one-click capture of open browser tabs including ChatGPT/Claude conversations

### Security

- Pre-commit hooks: ruff, mypy, bandit (Python vulnerability scanning), gitleaks (secret detection)
- Path traversal prevention for all vault write operations
- `scripts/scan_secrets.sh` for on-demand local secret scanning

### Infrastructure

- `src/` layout with editable install via `pip install -e .`
- CLI decomposed from monolithic router into per-command modules under `src/obsidian_ai_tools/commands/`
- GitHub Actions CI: lint, typecheck, complexity, coverage enforcement on every push and PR
- Comprehensive test suite (80%+ coverage) with unit and E2E golden-path tests
- GitNexus code-intelligence integration for safe symbol navigation and impact analysis
- Pydantic v2 settings with `.env` discovery (current dir → parents → `~/.kai/.env`)

[Unreleased]: https://github.com/larshansen1/obsidian-ai-tools/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/larshansen1/obsidian-ai-tools/releases/tag/v1.0.0
