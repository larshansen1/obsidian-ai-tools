# CLI Commands Documentation

Complete reference for all `kai` CLI commands.

## Table of Contents

- [Main Commands](#main-commands)
  - [ingest](#ingest)
  - [search](#search)
  - [rebuild-index](#rebuild-index)
  - [process-inbox](#process-inbox)
  - [preview](#preview)
  - [version](#version)

---

## Main Commands

### ingest

Ingest content into your Obsidian vault from various sources.

**Supported Sources:**
- YouTube videos (fetches transcript)
- Web articles (fetches text content)
- PDF documents (extracts text, local or remote)
- Local Markdown files (reads content)

**Usage:**
```bash
kai ingest <URL_OR_PATH> [OPTIONS]
```

**Arguments:**
- `url` - URL or file path to ingest (required)

**Options:**
- `--vault`, `-v` - Override vault path (default: from .env OBSIDIAN_VAULT_PATH)
- `--prompt-version`, `-p` - Prompt version (default: auto-detected based on source)
- `--max-pages` - Maximum pages to extract from PDF (default: 50)
- `--verbose` - Enable verbose logging
- `--transcript-providers` - Override provider order (comma-separated: direct,supadata,decodo)

**Examples:**
```bash
kai ingest https://www.youtube.com/watch?v=dQw4w9WgXcQ
kai ingest https://example.com/blog/article
kai ingest https://example.com/research-paper.pdf
kai ingest ./documents/paper.pdf --max-pages 30
kai ingest ./notes/draft.md
kai ingest <url> --model anthropic/claude-opus-4
kai ingest <url> --prompt-version youtube_v2
```

**Output:**
Generates a structured note with frontmatter including metadata, tags, and provenance tracking, saved to your vault's inbox folder.

---

### search

Search your Obsidian vault for notes using keyword, tag, and date filters.

**Usage:**
```bash
kai search [OPTIONS]
```

**Options:**
- `--keyword`, `-k` - Search for keyword in content
- `--tag`, `-t` - Filter by tag
- `--after` - Show notes created after date (YYYY-MM-DD)
- `--before` - Show notes created before date (YYYY-MM-DD)
- `--limit`, `-n` - Maximum number of results (default: 10)
- `--explain` - Show why each result matched (keywords, tags)
- `--no-boost` - Disable backlink-based score boosting
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai search --keyword "machine learning"
kai search --tag ai
kai search --keyword agents --tag llm
kai search --after 2026-01-01
kai search --keyword python --limit 5
kai search --keyword python --explain
kai search --keyword python --no-boost
```

**Output:**
Displays matching notes with title, tags, creation date, path, Obsidian URL, preview snippet, and outgoing wikilinks. Results are ranked by BM25F score boosted by backlink popularity (disable with `--no-boost`).

---

### rebuild-index

Rebuild the vault metadata index and full-text search index.

Forces a complete rebuild of the vault metadata index and the Whoosh search index.

**Usage:**
```bash
kai rebuild-index [OPTIONS]
```

**Options:**
- `--vault`, `-v` - Override vault path

**When to use:**
- After manually editing tags in notes
- When search results seem outdated
- After bulk operations on your vault
- To recover from index corruption

**Example:**
```bash
kai rebuild-index
```

**Output:**
```
🔄 Rebuilding indexes...
   📋 Rebuilding vault index...
      ✓ Indexed 42 note(s)
   🔍 Rebuilding search index...
      ✓ Search index rebuilt
✅ Index rebuild complete!
```

---

### process-inbox

Process inbox notes and move them to folders based on tag rules.

Reads `folder_rules.json` from vault root to map tags to folders. When a note has multiple matching tags, uses scoring to pick the best folder.

**Usage:**
```bash
kai process-inbox [OPTIONS]
```

**Options:**
- `--dry-run` - Preview changes without executing
- `--confirm` - Execute moves (prompts for confirmation unless --yes)
- `--yes`, `-y` - Skip confirmation prompt (requires --confirm)
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai process-inbox --dry-run              # Preview changes
kai process-inbox --confirm              # Execute with confirmation
kai process-inbox --confirm --yes        # Execute without confirmation
```

**Setup:**
Create `folder_rules.json` in your vault root:
```json
{
  "ai": "AI & Machine Learning",
  "llm": "AI & Machine Learning/LLMs",
  "python": "Development/Python"
}
```

**How it works:**
1. Scans all notes in inbox folder
2. Matches note tags against folder rules
3. When multiple tags match, picks most specific folder (deeper paths score higher)
4. Shows summary and asks for confirmation (unless dry-run)
5. Moves notes and tracks moves in `.kai/folder_mappings.jsonl`

---

### preview

Preview a URL before ingesting.

Shows metadata, estimated LLM cost, and key topics without full ingestion. Use this to decide whether a URL is worth ingesting.

**Usage:**
```bash
kai preview [URL] [OPTIONS]
```

**Arguments:**
- `url` - URL to preview (optional if using --batch)

**Options:**
- `--batch`, `-b` - Read URLs from stdin (one per line)
- `--interactive`, `-i` - Interactive mode with actions
- `--format`, `-f` - Output format: terminal, json (default: terminal)
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai preview https://youtube.com/watch?v=...
kai preview https://example.com/article
kai preview https://example.com/paper.pdf
pbpaste | kai preview --batch
kai preview URL --interactive
```

**Interactive Mode Actions:**
- `[i]` - Ingest now
- `[s]` - Save to reading list
- `[x]` - Skip

---

### version

Show version information.

**Usage:**
```bash
kai version
```

**Output:**
```
obsidian-ai-tools v1.0.0
Knowledge AI Tools for Obsidian
```

---

## Global Options

These options are available across multiple commands:

- `--vault`, `-v` - Override vault path (default: from .env OBSIDIAN_VAULT_PATH)
- `--dry-run` - Preview changes without executing (available in: process-inbox)
- `--confirm` - Confirm before modifying files (available in: process-inbox)
- `--yes`, `-y` - Skip confirmation prompts (available in: process-inbox)

### Confirmation Pattern

For destructive operations, the CLI follows this pattern:

**Phase 3 (Current - v1.0.0):**
- **Required:** Use `--confirm` for all destructive operations
- Use `--yes` / `-y` with `--confirm` to skip confirmation prompts
- `--dry-run` shows what would happen without making changes
- **Breaking:** Commands without `--confirm` will fail with an error

**Standard Pattern:**
```bash
# 1. Preview changes (safe)
kai <command> --dry-run

# 2. Execute with confirmation (safer)
kai <command> --confirm

# 3. Execute without confirmation (use with caution!)
kai <command> --confirm --yes
```

**Examples:**
```bash
# Process inbox
kai process-inbox --dry-run              # Preview
kai process-inbox --confirm              # Execute with prompt
kai process-inbox --confirm --yes        # Execute without prompt
```

---

## Configuration

All commands use settings from `.env` file:

```env
# OpenRouter Configuration
OPENROUTER_API_KEY=your_api_key_here

# Obsidian Vault Configuration
OBSIDIAN_VAULT_PATH=/path/to/your/vault
OBSIDIAN_INBOX_FOLDER=inbox

# LLM Configuration
LLM_MODEL=anthropic/claude-3.5-sonnet
MAX_TRANSCRIPT_LENGTH=50000
```

---

## Exit Codes

- `0` - Success
- `1` - Error occurred

---

## Generated Files

The CLI creates and maintains these files in your vault:

- `.kai/folder_mappings.jsonl` - Tracks note moves from process-inbox
- `.kai/whoosh_index/` - Full-text search index
- `.kai/observability.duckdb` - Cost and quality metrics database
- `.kai/reading_list.jsonl` - Saved URLs for later ingestion
- `.kai/vault_index.json` - Metadata cache for vault notes
- `.kai/temp_index.json` - Temporary index for folder scans

---

## Phase 3 Breaking Changes (v1.0.0)

### What Changed?

Phase 3 removes backward compatibility for destructive operations. The `--confirm` flag is now **strictly required** for all commands that modify existing files.

### Commands Affected

All destructive commands now require `--confirm`:
- `process-inbox` - Must use `--confirm`

### Migration from Phase 2

If you're still using old patterns:

**Old (Phase 2 - Deprecated):**
```bash
kai process-inbox                          # Now fails
```

**New (Phase 3 - Required):**
```bash
kai process-inbox --confirm
```

### Testing Your Workflow

1. Review commands that previously worked without `--confirm`
2. Add `--confirm` flag to all destructive commands
3. Use `--yes` with `--confirm` to skip confirmation prompts in scripts
4. Use `--dry-run` to preview changes safely

### Quick Reference

| Command | Pattern |
|---------|----------|
| `process-inbox` | `kai process-inbox --confirm` |
