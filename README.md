# Obsidian AI Tools

The goal is simple: make capturing valuable inputs into durable, well-structured knowledge feel satisfying enough that you actually do it.

`kai` is a local-first CLI that handles the friction between encountering something worth keeping and having it live usefully in your vault. Point it at a YouTube video, article, PDF, or local file — it fetches the content, generates a structured note through OpenRouter, and writes it to your inbox with tags, source metadata, and a summary you can actually use. The rest of the tool (search, organization, linking) exists to keep the vault healthy enough that ingesting into it stays rewarding.

## Features

- Multi-source ingestion for YouTube, web articles, local or remote PDFs, and local Markdown files
- Structured note generation through OpenRouter with versioned prompt templates
- YouTube transcript fallbacks through direct scraping, Supadata, and Decodo
- Rule-based inbox organization
- Whoosh BM25 full-text search with backlink boosting, tags, and date filters
- URL previews and DuckDB observability
- Optional local HTTP service and Chrome extension for browser-based ingestion
- Cache, circuit-breaker, retry, and rate-limiting support

## Quick Start

Get from zero to your first ingested note in under 10 minutes.

**Prerequisites:** Python 3.11+, git, an [OpenRouter](https://openrouter.ai/) API key, an existing Obsidian vault directory.

```bash
# 1. Clone and install
git clone https://github.com/larshansen1/obsidian-ai-tools.git
cd obsidian-ai-tools
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env — set at minimum:
#   OPENROUTER_API_KEY=your_key_here
#   OBSIDIAN_VAULT_PATH=/path/to/your/vault

# 3. Ingest your first URL
kai ingest "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Expected output:

```
✓ Fetched transcript (3421 tokens)
✓ Generated note via anthropic/claude-sonnet-4
✓ Written to /path/to/your/vault/inbox/Never Gonna Give You Up.md
```

The note lands in your vault's inbox folder with frontmatter (`title`, `tags`, `source_url`, `model`, `prompt_version`). Run `kai --help` to explore all commands.

## Requirements

- Python 3.11 or newer
- An [OpenRouter](https://openrouter.ai/) API key
- An existing Obsidian vault directory

YouTube ingestion can use free direct transcript fetching. Supadata and Decodo keys are optional fallbacks.

## Installation

```bash
git clone https://github.com/larshansen1/obsidian-ai-tools.git
cd obsidian-ai-tools

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For development tools:

```bash
python -m pip install -r requirements-dev.txt
```

## Configuration

Copy the example configuration:

```bash
cp .env.example .env
```

At minimum, configure:

```env
OPENROUTER_API_KEY=your_openrouter_api_key_here
OBSIDIAN_VAULT_PATH=/path/to/your/obsidian/vault
OBSIDIAN_INBOX_FOLDER=inbox
LLM_MODEL=anthropic/claude-sonnet-4
MAX_TRANSCRIPT_LENGTH=60000
```

`kai` searches for `.env` in the current directory and its parents, then falls back to `~/.kai/.env`.

Optional YouTube providers:

```env
# Metadata only
YOUTUBE_API_KEY=your_youtube_api_v3_key_here

# Transcript API fallbacks
SUPADATA_KEY=your_supadata_api_key_here
DECODO_API_KEY=your_decodo_basic_auth_token_here
YOUTUBE_TRANSCRIPT_PROVIDER_ORDER=direct,supadata,decodo
```

Additional cache, circuit-breaker, and PDF limits are documented in `.env.example`.

## Ingest Content

```bash
# YouTube video
kai ingest "https://www.youtube.com/watch?v=VIDEO_ID"

# Web article
kai ingest "https://example.com/blog/article"

# PDF, local or remote
kai ingest "https://example.com/research-paper.pdf"
kai ingest ./documents/research-paper.pdf --max-pages 30

# Local Markdown file
kai ingest ./notes/draft.md

# YouTube provider override
kai ingest "https://www.youtube.com/watch?v=VIDEO_ID" \
  --transcript-providers direct,supadata,decodo
```

`kai ingest` fetches the content, selects the matching prompt template, generates a note through OpenRouter, and writes the result to the configured inbox folder. Useful overrides include `--vault`, `--prompt-version`, `--max-pages`, and `--verbose`.

## Organize The Inbox

Create `folder_rules.json` in the root of your vault:

```json
{
  "ai": "AI & Machine Learning",
  "llm": "AI & Machine Learning/LLMs",
  "python": "Development/Python",
  "productivity": "Productivity"
}
```

An example is available in `folder_rules.json.example`.

```bash
# Preview moves
kai process-inbox --dry-run

# Execute moves with confirmation
kai process-inbox --confirm
```

Moves are recorded in `.kai/folder_mappings.jsonl` inside the vault.

## Search And Explore

```bash
# Full-text search with backlink boosting
kai search --keyword "machine learning"

# Combine filters
kai search --keyword agents --tag llm --after 2026-01-01 --limit 5

# Inspect scoring or disable backlink boosting
kai search --keyword python --explain
kai search --keyword python --no-boost

# Index maintenance
kai rebuild-index
```

## Preview

Preview URLs before committing them to the vault:

```bash
kai preview "https://example.com/article"
kai preview "https://example.com/article" --interactive
pbpaste | kai preview --batch
```

Interactive preview mode can ingest an item immediately.

## Observability

Ingestion metrics and OpenRouter costs are stored in `.kai/observability.duckdb` inside the vault.

```bash
kai usage
kai usage --days 7
kai usage --all
```

## Local HTTP Service

The repository includes a small FastAPI service and a Chrome extension for browser-based capture:

```bash
# Foreground
kai serve

# Detached
kai serve --background
kai serve --status
kai serve --status --log
kai serve --stop
```

The server binds to `127.0.0.1:8765` by default and exposes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/status` | Report the configured vault, inbox, and model |
| `POST` | `/ingest` | Run the full ingestion pipeline |
| `GET` | `/docs` | OpenAPI documentation |

To use the browser extension, start the server and load `chrome-extension/` as an unpacked extension in Chrome.
Reload the unpacked extension after updating it. When ingesting an open ChatGPT or Claude conversation,
the extension captures the rendered chat text from the authenticated browser tab before sending it to the
local service.

## Command Reference

Run `kai COMMAND --help` for all options.

| Command | Purpose |
| --- | --- |
| `kai ingest URL` | Generate a note from a supported source |
| `kai preview [URL]` | Preview URLs and estimate ingestion cost |
| `kai search` | Search vault content with keyword, tag, and date filters |
| `kai rebuild-index` | Rebuild metadata and Whoosh indexes |
| `kai process-inbox` | Move inbox notes according to tag rules |
| `kai usage` | Show command invocation counts and success rates |
| `kai serve` | Run the local ingestion service |
| `kai version` | Print the installed version |

## Generated Notes

Generated notes include frontmatter for traceability:

```markdown
---
title: Understanding AI Agents
tags:
  - ai
  - agents
created: 2026-01-24T10:30:00
type: source-note
source_type: youtube
source_url: https://youtube.com/watch?v=VIDEO_ID
model: anthropic/claude-sonnet-4
prompt_version: youtube_v2
---
```

Prompt templates live in `prompts/`.

## Development

```bash
# Tests
./scripts/test.sh

# Coverage
COVERAGE=1 ./scripts/test.sh

# Individual quality gates
make lint
make typecheck
make radon
make bandit

# Full local quality suite
make quality
```

The coverage threshold is currently `80%`, as configured in `pyproject.toml` (`fail_under = 80`).

## Data & Privacy

The `kai ingest` command sends note content to external LLM APIs:

| Command | Data sent externally |
|---------|---------------------|
| `kai ingest` | Source content (URL transcript, file text) |

**Provider chain:** Requests go to [OpenRouter](https://openrouter.ai) (`https://openrouter.ai/api/v1`), which forwards to the configured model provider (e.g. Anthropic, OpenAI). This tool does not store vault data server-side.

Data retention and training opt-out are governed by your OpenRouter account settings and the upstream model provider's privacy policy. By default, OpenRouter does not use submitted data for training — check [OpenRouter's privacy policy](https://openrouter.ai/privacy) and your chosen model provider's policy for the current terms.

The `OPENROUTER_API_KEY` environment variable determines which account (and therefore which data policy) applies.

See [DEVELOPMENT.md](DEVELOPMENT.md) for a detailed account of the AI-assisted development workflow, prompting strategy, and key lessons learned.

## Security

Keep `.env` files and API keys out of version control. Security checks run in CI on every push and PR (Bandit for Python vulnerabilities, Gitleaks for secrets). The same checks run locally via pre-commit hooks:

```bash
pre-commit run --all-files
```

To run them individually:

```bash
# Python vulnerability scan
bandit -c pyproject.toml -r src/

# Secret detection
gitleaks detect --source . --no-git --redact
```

## License

MIT
