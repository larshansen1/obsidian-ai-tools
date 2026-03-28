# Napkin-Inspired Search Features

Inspired by [@badlogicgames napkin](https://twitter.com/micLivs/status/...) — BM25/TF-IDF outperforms vector search for personal vault use cases. These features replace the embeddings pipeline with simpler, faster, and more effective primitives.

## Overview

Four changes, implemented in sequence:

1. **Remove embeddings** — delete Annoy + sentence-transformers pipeline
2. **`kai overview`** — vault terrain map: per-folder TF-IDF keywords + tags for agent context
3. **Wikilink traversal** — `kai follow <title>` + outgoing links in `kai search` output
4. **Backlink-boosted BM25** — structural graph signal in search ranking (default on, `--no-boost` to opt out)

---

## Prerequisite: `wikilinks.py` shared module

**New file**: `src/obsidian_ai_tools/wikilinks.py`

Eliminates the duplicate `WIKILINK_PATTERN` defined separately in `digest.py` and `concept_linking.py`.

Exports:
- `WIKILINK_PATTERN: re.Pattern` — `r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"`
- `extract_wikilinks(content: str) -> set[str]` — all wikilink targets (moved from `concept_linking.py`)
- `extract_top_wikilinks(content: str, n: int = 5) -> list[str]` — first N unique targets in document order
- `count_backlinks(vault_index: VaultIndex) -> dict[str, int]` — title → backlink count (moved from `digest.py`)
- `resolve_wikilink(target: str, vault_index: VaultIndex) -> NoteMetadata | None` — case-insensitive title match, falls back to filename stem

Migration:
- `digest.py`: remove `WIKILINK_PATTERN` (line 44) + `count_backlinks` (lines 128–148); import from `.wikilinks`
- `concept_linking.py`: remove `WIKILINK_PATTERN` (line 53) + `extract_wikilinks` (lines 56–66); import from `.wikilinks`

---

## Feature 1: Remove Embeddings

**Goal**: Delete the entire Annoy + sentence-transformers pipeline. Simplify the codebase, remove two heavy optional deps.

### Files to delete

| File | Reason |
|------|--------|
| `src/obsidian_ai_tools/embeddings.py` | Entire module |
| `tests/test_embeddings.py` | Entire test file |

### Files to modify

**`src/obsidian_ai_tools/search.py`**
- Remove `from .embeddings import Embedder, EmbeddingIndex, EmbeddingMatch` (line 12)
- Remove `hybrid`, `semantic`, `rerank_k`, `semantic_k` fields from `SearchQuery`
- Rewrite `search_notes()` — remove `embeddings_dir` + `embedder` params, remove semantic/hybrid branches
- Delete `_build_semantic_results()`
- Delete `_combine_rrf()`

**`src/obsidian_ai_tools/cli.py`** — `search` command
- Remove `--hybrid`, `--semantic`, `--rerank-k`, `--semantic-k` options
- Remove mode docs from docstring
- Remove `from .embeddings import build_embedding_index` (deferred import)
- Remove mutual-exclusion guards
- Remove `embeddings_dir` setup block
- Remove semantic fields from `SearchQuery(...)` constructor
- Remove `embeddings_dir=embeddings_dir` from `search_notes(...)` call

**`src/obsidian_ai_tools/cli.py`** — `rebuild_index` command
- Remove `from .embeddings import build_embedding_index` (deferred import)
- Delete Step 3 (embedding rebuild block, lines 521–533)

**`src/obsidian_ai_tools/config.py`**
- Remove `embedding_model` field (lines 57–61)

**`pyproject.toml`**
- Remove `"annoy>=1.17.3"` and `"sentence-transformers>=2.7.0"` from dependencies

**`requirements.txt`**
- Remove `annoy>=1.17.3` and `sentence-transformers>=2.7.0`

**`tests/test_search.py`**
- Remove `from obsidian_ai_tools.embeddings import build_embedding_index` (line 10)
- Remove `DummyEmbedder` class (lines 20–36)
- Remove `semantic_vault` fixture (lines 473–517)
- Remove `test_hybrid_search_combines_results`, `test_semantic_search_respects_tag_filter`, semantic half of `test_search_explain_adds_reason`
- Remove `hybrid`, `semantic`, `rerank_k`, `semantic_k` assertions from `TestSearchQuery`

**`tests/test_cli.py`**
- Unwrap `test_rebuild_index_command` from `with patch("obsidian_ai_tools.embeddings.build_embedding_index")` block
- Remove `build_embedding_index.assert_called_once()` assertion

---

## Feature 2: `kai overview`

**Goal**: Vault terrain map. Produces per-folder keyword extraction (inter-folder TF-IDF) + note counts + top tags. Primarily designed to inject into an agent's system prompt before searching.

### New file: `src/obsidian_ai_tools/overview.py`

**Data models**:
```python
class FolderSummary(BaseModel):
    folder: str                         # relative path from vault root; "(root)" for top-level
    note_count: int
    top_keywords: list[str]             # top N TF-IDF terms; inter-folder IDF suppresses common terms
    top_tags: list[tuple[str, int]]     # (tag, count) sorted by count desc

class VaultOverview(BaseModel):
    vault_path: Path
    total_notes: int
    total_folders: int
    folders: list[FolderSummary]        # sorted alphabetically
    generated_at: datetime = Field(default_factory=datetime.now)
```

**Functions**:
```python
def generate_overview(vault_path: Path, top_n: int = 8) -> VaultOverview
def format_overview_terminal(overview: VaultOverview) -> str
def format_overview_markdown(overview: VaultOverview) -> str
def format_overview_json(overview: VaultOverview) -> str
def format_overview_compact(overview: VaultOverview) -> str   # for agent injection
```

**TF-IDF approach**: One document per folder (all note content concatenated). This makes IDF inter-folder: terms appearing across many folders get low weight; folder-specific vocabulary scores high. Uses `TfidfVectorizer(stop_words="english")` from scikit-learn (already a dependency). Guard: if only one folder exists, fall back to per-note TF-IDF within that folder.

**Compact format** (agent injection):
```
vault: 247 notes, 12 folders
---
AI/ (43 notes) | keywords: transformer attention llm rag retrieval | tags: ai(31) llm(18)
Development/ (28 notes) | keywords: python async docker kubernetes | tags: python(20) backend(15)
```
One line per folder, pipe-delimited, no extra whitespace.

### CLI command

```
kai overview [--format terminal|markdown|json|compact] [--top-n N] [--vault PATH]
```

- Default format: `terminal`
- Default `--top-n`: 8
- Added to `cli.py` after the `digest` command block
- Follows the exact `digest` command pattern: lazy imports, `get_settings()` with `typer.Exit(1)`, `vault_path = vault or settings.obsidian_vault_path`

### New test files

- `tests/test_overview.py` — unit tests for `generate_overview` and all 4 formatters
- Tests in `tests/test_cli.py` for `kai overview` terminal/compact/json formats + config error handling

---

## Feature 3: Wikilink Traversal

**Goal**: Let an agent resolve `[[wikilinks]]` by title and navigate the vault's link graph.

### Part A: `kai follow <note-title>`

New CLI command. Resolves a wikilink target to a note and prints the raw file content (frontmatter + body) to stdout.

```
kai follow "Attention Mechanisms"
kai follow "attention-mechanisms"
```

Resolution order:
1. Case-insensitive `note.title` match
2. Case-insensitive `note.file_path.stem` match
3. Exit 1 with error message if not found

Implementation: Lives in `cli.py` using `resolve_wikilink()` from `wikilinks.py`. Reads raw file (not `note.content` which has frontmatter stripped) via `note.file_path.read_text()`.

### Part B: Outgoing links in `kai search` output

`SearchResult` gets a new field:
```python
outgoing_links: list[str] = Field(default_factory=list)
```

Populated in `_search_whoosh()` using `extract_top_wikilinks(note.content, n=5)` from `wikilinks.py`.

Output in `kai search` display loop:
```
   Links: [[Attention Mechanisms]]  [[Transformers]]  [[BERT]]
```

No changes to `_combine_rrf` — the field propagates naturally since results are built upstream.

### Test additions

- `tests/test_wikilinks.py` — unit tests for all 5 functions in `wikilinks.py`
- `tests/test_cli.py` — tests for `kai follow` (title match, stem match, not found), wikilink display in `kai search`

---

## Feature 4: Backlink-Boosted BM25

**Goal**: Notes linked to by many other notes are ranked higher. Default on; `--no-boost` to disable.

### Approach: post-query rerank

After Whoosh returns BM25F results, multiply each score by a backlink factor:

```
boost = 1 + log(backlink_count + 1)
final_score = bm25_score * boost
```

A note with 0 backlinks: `boost = 1.0` (neutral, score unchanged).
A note with 9 backlinks: `boost ≈ 3.3`.
A note with 99 backlinks: `boost ≈ 5.6`.

Post-query rerank is preferred over a custom Whoosh scorer because:
- Avoids Whoosh internal coupling (`stored_fields(docnum)` in hot path)
- Simple: one dict lookup per result after retrieval
- Backlink dict is computed once before the query (O(total_chars))

### Changes to `search.py`

Add to `SearchQuery`:
```python
no_boost: bool = Field(default=False, description="Disable backlink score boosting")
```

New helper:
```python
def _apply_backlink_boost(
    results: list[SearchResult],
    backlinks: dict[str, int],
) -> list[SearchResult]:
    boosted = []
    for result in results:
        count = backlinks.get(result.note.title, 0)
        boost = 1.0 + math.log(count + 1)
        boosted.append(result.model_copy(update={"score": result.score * boost}))
    return sorted(boosted, key=lambda r: r.score, reverse=True)
```

Updated `search_notes()`:
```python
def search_notes(
    query: SearchQuery,
    vault_index: VaultIndex,
    index_dir: Path,
    backlinks: dict[str, int] | None = None,
) -> list[SearchResult]:
    notes_by_path = {note.file_path: note for note in vault_index.notes}
    results = _search_whoosh(query, vault_index, index_dir, query.limit, notes_by_path)
    if not query.no_boost and backlinks:
        results = _apply_backlink_boost(results, backlinks)
    return results
```

### Changes to `cli.py` — `search` command

Add parameter:
```python
no_boost: Annotated[bool, typer.Option("--no-boost", help="Disable backlink score boosting")] = False,
```

After `build_whoosh_index(...)`:
```python
from .wikilinks import count_backlinks
backlinks = count_backlinks(vault_index)
```

Pass `no_boost=no_boost` to `SearchQuery(...)` and `backlinks=backlinks` to `search_notes(...)`.

### New tests in `tests/test_search.py`

New class `TestBacklinkBoost`:
- `test_boost_promotes_linked_note` — linked note scores higher with boost on
- `test_no_boost_flag_disables_reranking` — scores equal raw BM25F with `no_boost=True`
- `test_zero_backlinks_neutral_boost` — 0 backlinks → boost = 1.0, score unchanged
- `test_apply_backlink_boost_ordering` — unit test `_apply_backlink_boost` directly

---

## Implementation Order

```
Step 0: wikilinks.py (shared foundation, pure refactor — must be green before proceeding)
Step 1: Embeddings removal
Step 2: kai overview
Step 3: Wikilink traversal (kai follow + outgoing links in search)
Step 4: Backlink-boosted BM25
```

## Files Summary

| File | Action |
|------|--------|
| `src/obsidian_ai_tools/wikilinks.py` | CREATE |
| `src/obsidian_ai_tools/overview.py` | CREATE |
| `src/obsidian_ai_tools/embeddings.py` | DELETE |
| `tests/test_embeddings.py` | DELETE |
| `src/obsidian_ai_tools/search.py` | MODIFY (remove semantic, add boost) |
| `src/obsidian_ai_tools/cli.py` | MODIFY (remove semantic, add overview/follow/no-boost) |
| `src/obsidian_ai_tools/config.py` | MODIFY (remove embedding_model) |
| `src/obsidian_ai_tools/digest.py` | MODIFY (import from wikilinks) |
| `src/obsidian_ai_tools/concept_linking.py` | MODIFY (import from wikilinks) |
| `pyproject.toml` | MODIFY (remove annoy + sentence-transformers) |
| `requirements.txt` | MODIFY (remove annoy + sentence-transformers) |
| `tests/test_wikilinks.py` | CREATE |
| `tests/test_overview.py` | CREATE |
| `tests/test_search.py` | MODIFY (remove semantic tests, add boost tests) |
| `tests/test_cli.py` | MODIFY (remove embedding patch, add overview/follow tests) |
