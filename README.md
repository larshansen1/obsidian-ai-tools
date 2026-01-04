# Obsidian AI Tools

AI-powered tools for Obsidian knowledge management. Week 1 MVP: YouTube video ingestion with LLM-generated structured notes.

## Features

- **Multi-Source Ingestion**: YouTube videos, web articles, PDFs, and local Markdown files
- **AI-Powered Processing**: Uses LLMs (via OpenRouter) to generate structured notes
- **Smart Organization**: Folder rules to automatically organize notes by tags
- **Full-Text Search**: Whoosh-powered search across your vault
- **Robust Architecture**: Built-in caching, circuit breakers, rate limiting, and provider fallbacks
- 🤖 **LLM-Powered Summarization**: Uses OpenRouter (Claude 3.5 Sonnet) to extract key insights
- 📝 **Structured Notes**: Consistent frontmatter with metadata, tags, and provenance tracking
- ⚡ **CLI Interface**: Simple `kai` command with multiple subcommands
- 🔧 **MCP-Ready**: Pure functions designed for easy Model Context Protocol integration
- ✅ **Quality Gates**: Ruff, mypy, pytest with comprehensive testing

## Quick Start

### Prerequisites

- Python 3.12+
- OpenRouter API key ([get one here](https://openrouter.ai/))
- An Obsidian vault

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd obsidian-ai-tools
   ```

2. **Create virtual environment**
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install package**
   ```bash
   pip install -e .
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

### Configuration

Create a `.env` file in the project root:

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

## Usage

### Basic Usage

Ingest a YouTube video:

### Ingest Content

```bash
# YouTube videos
kai ingest https://www.youtube.com/watch?v=VIDEO_ID

# Web articles
kai ingest https://example.com/blog/article

# PDF documents (local or remote)
kai ingest https://arxiv.org/pdf/2024.12345.pdf
kai ingest ./documents/research-paper.pdf --max-pages 30

# Local Markdown files
kai ingest ./notes/draft.md

# Custom prompt version
kai ingest URL --prompt-version youtube_v2

# Custom vault path
kai ingest URL --vault /path/to/other/vault
```

Output:
```
🎥 Ingesting YouTube video...
   URL: https://www.youtube.com/watch?v=dQw4w9WgXcQ
📥 Fetching transcript...
   ✓ Transcript fetched (15234 chars)
🤖 Generating note with anthropic/claude-3.5-sonnet...
   ✓ Note generated: 'Understanding AI Agents'
   ✓ Tags: ai, agents, llm
💾 Writing note to vault...
   ✓ Note saved to: /path/to/vault/inbox/youtube-understanding-ai-agents.md
✅ Ingestion complete!
```

### Advanced Usage

**Override model:**
```bash
kai ingest <url> --model anthropic/claude-opus-4
```

**Override inbox folder:**
```bash
kai ingest <url> --inbox research
```

**Override vault path:**
```bash
kai ingest <url> --vault /different/vault/path
```

**Show version:**
```bash
kai version
```

### Inbox Organization

Automatically organize notes from your inbox into folders based on tags:

```bash
# Preview changes (dry run)
kai process-inbox --dry-run

# Execute moves
kai process-inbox
```

**Setup:**

1. Create `folder_rules.json` in your vault root:
   ```json
   {
     "ai": "AI & Machine Learning",
     "llm": "AI & Machine Learning/LLMs",
     "python": "Development/Python",
     "productivity": "Productivity"
   }
   ```

2. Copy the example file:
   ```bash
   cp folder_rules.json.example /path/to/vault/folder_rules.json
   ```

**How it works:**
- Scans all notes in your inbox folder
- Matches note tags against your folder rules
- When multiple tags match, picks the most specific folder (deeper paths score higher)
- Shows summary and asks for confirmation
- Moves notes and tracks moves in `.kai/folder_mappings.jsonl`
- Notes without matching tags stay in inbox
- Reports any files that couldn't be parsed

**Example output:**
```
📂 Loading folder rules...
   ✓ Loaded 8 rule(s)
📥 Scanning inbox for notes...
📋 Found 3 note(s) to move:

  📄 youtube-understanding-agents.md
     Tags: ai, llm, agents
     → AI & Machine Learning/LLMs (matched: llm, score: 1.2)

  📄 article-python-tips.md
     Tags: python, programming
     → Development/Python (matched: python, score: 1.0)

❓ Move 3 note(s)? [y/n]: y

💾 Moving notes...
   ✓ Moved youtube-understanding-agents.md → AI & Machine Learning/LLMs
   ✓ Moved article-python-tips.md → Development/Python

✅ Successfully moved 3/3 note(s)
```

### Vault Search

Search your vault for notes using keyword, tag, and date filters:

```bash
# Search by keyword
kai search --keyword "machine learning"

# Search by tag
kai search --tag ai

# Combine filters
kai search --keyword agents --tag llm

# Filter by date range
kai search --after 2026-01-01 --before 2026-12-31

# Limit results
kai search --keyword python --limit 5
```

**Example output:**
```
🔍 Searching vault...
   Found 2 result(s):

1. Understanding AI Agents
   Tags: ai, llm, agents
   Created: 2026-01-02
   Path: /vault/AI & Machine Learning/LLMs/understanding-ai-agents.md
   Open: obsidian://open?vault=MyVault&file=...
   Preview: This video explores how AI agents work...

2. Building Python Agents
   Tags: python, ai, agents
   Created: 2026-01-01
   Path: /vault/Development/Python/building-python-agents.md
   Open: obsidian://open?vault=MyVault&file=...
```

### Tag Management

List all tags in your vault with counts:

```bash
kai list-tags
```

**Example output:**
```
📋 Listing tags...
   Found 12 unique tag(s):

   ai: 15 note(s)
   python: 8 note(s)
   llm: 6 note(s)
   productivity: 4 note(s)
```

### Index Management

Rebuild the vault indexes when needed:

```bash
kai rebuild-index
```

**When to use:**
- After manually editing tags in notes
- When search results seem outdated
- After bulk operations on your vault
- To recover from index corruption

**Example output:**
```
🔄 Rebuilding indexes...
   📋 Rebuilding vault index...
      ✓ Indexed 42 note(s)
   🔍 Rebuilding search index...
      ✓ Search index rebuilt
✅ Index rebuild complete!
```

## Generated Note Structure

Notes follow this schema (defined in `prompts/youtube_v1.md`):

```markdown
---
type: source-note
source_type: youtube
source_url: https://youtube.com/watch?v=xxx
created: 2025-01-24T10:30:00
model: anthropic/claude-3.5-sonnet
prompt_version: youtube_v1
tags:
  - ai
  - agents
  - productivity
---

# Understanding AI Agents

## Summary

A 2-3 sentence summary of the video content...

## Key Points

- First key insight or takeaway
- Second key insight or takeaway
- Third key insight or takeaway

## Source

[Original Video](https://youtube.com/watch?v=xxx)
```

### Provenance Tracking

Every note includes:
- ✅ Source URL
- ✅ Timestamp of creation
- ✅ LLM model used
- ✅ Prompt version
- ✅ Source type

This enables audit trails and future re-processing with improved prompts.

## Development

### Running Tests

```bash
# All tests
pytest tests/

# With coverage
pytest tests/ --cov=src/obsidian_ai_tools --cov-report=term-missing

# Verbose
pytest tests/ -v
```

### Quality Gates

```bash
# Linting
ruff check src/

# Type checking
mypy src/ --config-file mypy.ini

# Format code
ruff format src/

# Run all quality checks
make quality  # or: ruff check && mypy src/ && pytest tests/
```

### Project Structure

```
obsidian-ai-tools/
├── src/obsidian_ai_tools/    # Source code
│   ├── config.py             # Pydantic settings
│   ├── models.py             # Data models
│   ├── youtube.py            # YouTube transcript fetching
│   ├── llm.py                # OpenRouter LLM integration
│   ├── obsidian.py           # Vault file operations
│   └── cli.py                # Typer CLI
├── tests/                    # Unit tests
├── prompts/                  # LLM prompt templates
│   └── youtube_v1.md
├── .env.example              # Example configuration
├── pyproject.toml            # Project metadata & deps
├── ARCHITECTURE.md           # Architecture & migration guide
└── README.md                 # This file
```

## Architecture

This project uses a **pragmatic functional architecture** optimized for:
- Fast iteration (Week 1 tracer bullet)
- Pure functions (MCP-ready)
- Clear module boundaries
- Easy testing

For details on the current architecture and **enterprise migration path**, see [ARCHITECTURE.md](./ARCHITECTURE.md).

### Key Design Decisions

1. **Pure Functions**: Core logic in `youtube.py`, `llm.py`, `obsidian.py` has no side effects
2. **Pydantic Settings**: Type-safe configuration with environment variable support
3. **Explicit Error Types**: Each module defines its own exception hierarchy
4. **Prompt Templates**: Versioned markdown files (not hardcoded strings)

## Roadmap

### Cycle 1: Tracer Bullet ✅
- [x] YouTube transcript ingestion
- [x] LLM note generation via OpenRouter
- [x] CLI interface (`kai ingest`)
- [x] Provenance tracking
- [x] Quality gates (ruff, mypy, pytest)

### Cycle 2: Multi-Source Ingestion ✅
- [x] Web article ingestion
- [x] Markdown file ingestion  
- [x] Provider abstraction pattern
- [x] Error handling and validation
- [x] Rate limiting and retries

### Cycle 3: Inbox Organization ✅
- [x] Rule-based folder organization
- [x] Batch processing with dry-run mode
- [x] Move tracking and audit logs
- [x] Path security validation

### Cycle 3.5: Vault Search & Index Management ✅
- [x] Full-text keyword search (`kai search`)
- [x] Tag-based filtering
- [x] Date range queries
- [x] Tag listing (`kai list-tags`)
- [x] Index rebuild command (`kai rebuild-index`)
- [x] Recursive vault scanning

### Future Cycles
- [ ] MCP server implementation
- [ ] Integration with Open WebUI
- [ ] Multi-source support (articles, PDFs)
- [ ] Tag normalization

### Future
- [ ] Semantic search (vector embeddings)
- [ ] Automated evaluation (LLM-as-judge)
- [ ] Human-in-the-loop approval workflows
- [ ] Cost tracking and governance

## Troubleshooting

### "No transcript available"

- Video may not have English captions
- Video may be private or restricted
- Try a different video to verify setup

### "Configuration error"

- Ensure `.env` file exists and has all required fields
- Check that `OBSIDIAN_VAULT_PATH` points to a valid directory
- Verify `OPENROUTER_API_KEY` is set

### "Failed to generate note"

- Check OpenRouter API key is valid
- Verify you have API credits
- Check transcript length isn't exceeding `MAX_TRANSCRIPT_LENGTH`

### Coverage warnings

Current coverage threshold is set to 45% for MVP. This excludes:
- `cli.py` (integration tested via E2E)
- `config.py` (requires environment setup)

Production target is 85%+. See [ARCHITECTURE.md](./ARCHITECTURE.md) for testing strategy.

## Contributing

This is a personal learning project following the [Tracer Bullet Approach](https://github.com/larshansen1/obsidian-ai-tools/docs/Tracer%20Bullet%20Approach.md).

Contributions welcome, but please note this is an intentionally iterative learning project where simplicity and shipping quickly are prioritized over perfection.

## License

MIT

## Acknowledgments

- Built as part of [Personal AI Projects Learning Roadmap](https://github.com/larshansen1/obsidian-ai-tools/docs/Learning%20Roadmap.md)
- Uses [OpenRouter](https://openrouter.ai/) for LLM access
- Inspired by the Pragmatic Programmer's Tracer Bullet methodology
