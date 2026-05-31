# CLI Commands Documentation

Complete reference for all `kai` CLI commands.

## Table of Contents

- [Main Commands](#main-commands)
  - [ingest](#ingest)
  - [search](#search)
  - [list-tags](#list-tags)
  - [rebuild-index](#rebuild-index)
  - [process-inbox](#process-inbox)
  - [update-rules](#update-rules)
  - [stats](#stats)
  - [quality](#quality)
  - [digest](#digest)
  - [overview](#overview)
  - [follow](#follow)
  - [preview](#preview)
  - [connect](#connect)
  - [refresh](#refresh)
  - [tags](#tags)
  - [version](#version)
- [Reading List Commands](#reading-list-commands)
  - [reading-list list](#reading-list-list)
  - [reading-list ingest](#reading-list-ingest)
  - [reading-list clear](#reading-list-clear)

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

### list-tags

List all tags in your vault with counts.

**Usage:**
```bash
kai list-tags [OPTIONS]
```

**Options:**
- `--vault`, `-v` - Override vault path
- `--by-folder`, `-f` - Group tags by folder

**Examples:**
```bash
kai list-tags
kai list-tags --by-folder
```

**Output:**
Shows all unique tags with note counts, optionally grouped by folder.

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

### update-rules

Suggest and optionally add folder rules for inbox notes that do not match existing rules.

Scans unprocessed inbox notes, aggregates their tags, and suggests new `folder_rules.json` entries. Existing rules are preserved.

**Usage:**
```bash
kai update-rules [OPTIONS]
```

**Options:**
- `--confirm` - Update `folder_rules.json` (prompts for confirmation unless --yes)
- `--yes`, `-y` - Skip confirmation prompt (requires --confirm)
- `--vault`, `-v` - Override vault path
- `--min-notes` - Minimum unprocessed inbox notes a tag must appear in (default: 2)
- `--max-suggestions` - Maximum rule suggestions to show (default: 10)
- `--include-singletons` - Include tags used by only one unprocessed note

**Examples:**
```bash
kai update-rules                     # Preview suggested rules
kai update-rules --include-singletons # Include one-off tags
kai update-rules --confirm           # Update with confirmation
kai update-rules --confirm --yes     # Update without prompt
```

**How it works:**
1. Loads existing rules from `folder_rules.json` if present
2. Scans inbox notes that do not match any existing rule
3. Suggests recurring missing tag-to-folder mappings from those notes' tags
4. Prefers matching existing vault folders and existing rule destinations
5. Writes the merged rules file only with `--confirm`

---

### stats

Show LLM API cost statistics.

**Usage:**
```bash
kai stats [OPTIONS]
```

**Options:**
- `--days`, `-d` - Number of days to include in summary (default: 30)
- `--recent`, `-r` - Show recent individual requests

**Examples:**
```bash
kai stats
kai stats --days 7
kai stats --recent
```

**Output:**
Displays:
- Total costs for the period
- Costs breakdown by model
- Costs breakdown by source type
- Costs breakdown by operation
- Recent costs (last 7 days)

---

### quality

Show ingestion quality metrics.

**Usage:**
```bash
kai quality [OPTIONS]
```

**Options:**
- `--days`, `-d` - Number of days to include in summary (default: 30)

**Examples:**
```bash
kai quality
kai quality --days 7
kai quality --days 90
```

**Output:**
Displays:
- Success rates by source type
- Average processing times
- Common errors

---

### digest

Generate a knowledge digest for the specified period.

Summarizes vault activity including new notes, top tags, most referenced notes, and inbox status.

**Usage:**
```bash
kai digest [OPTIONS]
```

**Options:**
- `--days`, `-d` - Number of days to include in digest (default: 7)
- `--output`, `-o` - Save to vault inbox with this filename
- `--format`, `-f` - Output format: terminal, markdown, json (default: terminal)
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai digest                           # Weekly summary to terminal
kai digest --days 1                  # Daily summary
kai digest --output weekly-review    # Save to vault inbox
kai digest --format json             # JSON output
```

---

### overview

Generate a vault terrain map showing per-folder note counts, top keywords, and tag distributions.

Useful for understanding the shape of your vault at a glance or injecting vault context into an agent system prompt.

**Usage:**
```bash
kai overview [OPTIONS]
```

**Options:**
- `--format`, `-f` - Output format: terminal, markdown, json, compact (default: terminal)
- `--top-n` - Number of top keywords per folder (default: 5)
- `--vault`, `-v` - Override vault path

**Formats:**
- `terminal` — Human-readable table (default)
- `markdown` — Markdown document suitable for saving to vault
- `json` — Machine-readable JSON
- `compact` — One-line-per-folder pipe-delimited format for agent injection

**Examples:**
```bash
kai overview                          # Terminal overview
kai overview --format markdown        # Save-ready markdown
kai overview --format compact         # Agent system prompt injection
kai overview --format json            # JSON output
kai overview --top-n 10               # More keywords per folder
```

**Example compact output:**
```
inbox/ (12 notes) | keywords: python, llm, agents | tags: ai:8, python:5
AI/ (34 notes) | keywords: attention, transformer, embeddings | tags: ai:34, llm:20
```

---

### follow

Follow outgoing wikilinks from a note and display the raw file content and its wikilinks.

**Usage:**
```bash
kai follow <TITLE> [OPTIONS]
```

**Arguments:**
- `title` - Title of the note to follow (required)

**Options:**
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai follow "Attention Mechanisms"
kai follow "Python Basics"
```

**How it works:**
1. Resolves the note by title (case-insensitive) or filename stem
2. Displays the full raw file content including frontmatter
3. Lists all outgoing `[[wikilinks]]` found in the note

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

### connect

Find related notes and suggest connections.

Uses TF-IDF similarity to discover notes with similar content. Can scan a folder for all connections, a single note, or detect orphans.

**Usage:**
```bash
kai connect [OPTIONS]
```

**Options:**
- `--note`, `-n` - Path to note (relative to vault)
- `--folder`, `-f` - Scan folder for all connections
- `--orphans` - Find orphan notes with no links
- `--threshold`, `-t` - Minimum similarity score (0-1) (default: 0.3)
- `--top` - Maximum suggestions per note (default: 5)
- `--auto-link` - Auto-insert wikilinks
- `--confirm` - Confirm before modifying files
- `--yes`, `-y` - Skip confirmation prompts (requires --confirm)
- `--dry-run` - Preview changes without modifying
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai connect --folder "AI/LLMs"                              # Read-only analysis
kai connect --note "AI/Attention.md"                        # Read-only analysis
kai connect --orphans                                       # Find orphans
kai connect --folder "AI" --auto-link --confirm             # Insert links with prompt
kai connect --folder "AI" --auto-link --confirm --yes       # Insert links without prompt
```

**Output:**
Lists related notes with similarity scores and shared keywords. Can automatically insert wikilinks.

---

### refresh

Re-process notes with a new prompt version.

Update old notes generated with outdated prompts to use improved prompt templates. Creates backups before modifying files.

**Usage:**
```bash
kai refresh [OPTIONS]
```

**Options:**
- `--prompt-version`, `-p` - Target prompt version (e.g., youtube_v2) (required)
- `--tag`, `-t` - Filter by tag
- `--current`, `-c` - Only refresh notes with this prompt version
- `--since`, `-s` - Only notes older than N days
- `--dry-run` - List candidates without refreshing
- `--show-diff` - Show preview for a specific note path
- `--confirm` - Execute refresh (creates backups)
- `--yes`, `-y` - Skip confirmation prompts (requires --confirm)
- `--no-backup` - Skip backup creation (dangerous)
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai refresh -p youtube_v2 --dry-run                       # List candidates
kai refresh -p youtube_v2 --tag ai --dry-run              # Filter by tag
kai refresh -p youtube_v2 --show-diff "AI/Attention.md"   # Preview changes
kai refresh -p youtube_v2 --tag ai --confirm              # Execute with confirmation
kai refresh -p youtube_v2 --tag ai --confirm --yes        # Execute without confirmation
```

**Warning:**
Always review with `--dry-run` first. Backups are created by default unless `--no-backup` is used.

---

### tags

Analyze and fix tag hygiene issues.

Detects near-duplicate tags, high co-occurrence patterns, and orphan tags. Can automatically apply consolidation fixes.

**Usage:**
```bash
kai tags [OPTIONS]
```

**Options:**
- `--confirm` - Execute fixes (prompts unless `--yes`)
- `--plan` - Output JSON plan for review
- `--apply` - Apply fixes from plan file
- `--yes`, `-y` - Auto-accept all suggestions (requires `--confirm`)
- `--check`, `-c` - Run specific check: similar, cooccurrence, orphans
- `--threshold`, `-t` - Similarity threshold for tag matching (0-1) (default: 0.8)
- `--min-overlap` - Minimum co-occurrence count to report (default: 3)
- `--vault`, `-v` - Override vault path

**Examples:**
```bash
kai tags                        # Show issues (read-only)
kai tags --confirm              # Interactive fixes
kai tags --confirm --yes        # Auto-fix all
kai tags --plan > plan.json     # Generate plan
kai tags --apply plan.json --confirm # Apply plan with confirmation
kai tags --check similar        # Run only similar tag check
```

**Output:**
Analysis report showing:
- Similar tags (consider consolidating)
- High co-occurrence patterns (often used together)
- Orphan tags (used only once)

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

## Reading List Commands

Manage your reading list of saved URLs.

### reading-list list

List items in your reading list.

**Usage:**
```bash
kai reading-list list [OPTIONS]
```

**Options:**
- `--vault`, `-v` - Override vault path
- `--status`, `-s` - Filter by status: pending, ingested, skipped

**Examples:**
```bash
kai reading-list list
kai reading-list list --status pending
```

---

### reading-list ingest

Ingest the next pending item from your reading list.

**Usage:**
```bash
kai reading-list ingest [OPTIONS]
```

**Options:**
- `--vault`, `-v` - Override vault path
- `--all`, `-a` - Ingest all pending items

**Examples:**
```bash
kai reading-list ingest
kai reading-list ingest --all
```

---

### reading-list clear

Clear completed items from reading list.

**Usage:**
```bash
kai reading-list clear [OPTIONS]
```

**Options:**
- `--vault`, `-v` - Override vault path
- `--status`, `-s` - Status to clear: ingested, skipped, all (default: ingested)
- `--confirm` - Execute clear (prompts for confirmation unless --yes)
- `--yes`, `-y` - Skip confirmation prompt (requires --confirm)

**Examples:**
```bash
kai reading-list clear --confirm                       # Clear ingested with prompt
kai reading-list clear --confirm --yes                 # Clear ingested without prompt
kai reading-list clear --status skipped --confirm      # Clear skipped
kai reading-list clear --status all --confirm          # Clear everything
```

---

## Global Options

These options are available across multiple commands:

- `--vault`, `-v` - Override vault path (default: from .env OBSIDIAN_VAULT_PATH)
- `--dry-run` - Preview changes without executing (available in: process-inbox, connect, refresh)
- `--confirm` - Confirm before modifying files (available in: connect, refresh)
- `--yes`, `-y` - Skip confirmation prompts (available in: process-inbox, connect, refresh, tags, reading-list clear)

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

# Connect with auto-linking
kai connect --folder "AI" --auto-link --confirm       # Prompts before inserting
kai connect --folder "AI" --auto-link --confirm --yes # No prompt

# Tag hygiene
kai tags --confirm                       # Interactive fixes
kai tags --confirm --yes                 # Auto-fix all

# Refresh notes
kai refresh -p youtube_v2 --confirm      # Requires --confirm
kai refresh -p youtube_v2 --confirm --yes # No prompt
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
- `connect --auto-link` - Must use `--confirm`
- `reading-list clear` - Must use `--confirm`
- `tags --fix` → **Removed** - Use `tags --confirm` instead
- `refresh` - No change (already required --confirm)

### Migration from Phase 2

If you're still using old patterns:

**Old (Phase 2 - Deprecated):**
```bash
kai process-inbox                          # Now fails
kai connect --folder "AI" --auto-link      # Now fails
kai reading-list clear                     # Now fails
kai tags --fix                             # Now fails (flag removed)
```

**New (Phase 3 - Required):**
```bash
kai process-inbox --confirm
kai connect --folder "AI" --auto-link --confirm
kai reading-list clear --confirm
kai tags --confirm
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
| `connect` (auto-link) | `kai connect --folder X --auto-link --confirm` |
| `tags` | `kai tags --confirm` |
| `reading-list clear` | `kai reading-list clear --confirm` |
| `refresh` | `kai refresh -p X --confirm` |
