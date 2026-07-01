# Search and Analysis Algorithms

This document describes the algorithms, indexes, and methods used by the `kai` CLI commands for search, concept linking, and tag management.

## Table of Contents

1. [kai search](#kai-search)

---

## kai search

The `kai search` command performs full-text search across an Obsidian vault using the **Whoosh** search engine library.

### Index Structure

**Whoosh Schema** (`search.py:32-42`):
```python
Schema(
    file_path=ID(stored=True, unique=True),  # Note identifier
    title=TEXT(stored=True),                  # Note title (searchable + stored)
    content=TEXT(stored=True),                # Full content (searchable)
    tags=KEYWORD(stored=True, commas=True, scorable=True),  # Tag list
    author=TEXT(stored=True),                 # Author field
    source_url=ID(stored=True),               # Source URL for ingested content
    created=DATETIME(stored=True),            # Creation date
)
```

### Index Building Process

**Function**: `build_whoosh_index(vault_index, index_dir)` (`search.py:45-72`)

1. Creates Whoosh index directory if needed
2. Creates a new index using `index.create_in()`
3. Iterates through all notes in the VaultIndex
4. For each note, adds a document with:
   - `file_path`: Unique identifier
   - `title`: Note title text
   - `content`: Full note content
   - `tags`: Comma-separated tag list
   - `author`, `source_url`, `created`: Metadata fields
5. Commits the writer to persist the index

### Search Algorithm

**Function**: `search_notes(query, vault_index, index_dir)` (`search.py:75-157`)

**Query Processing**:

1. **Keyword Search** (`search.py:101-104`):
   - Uses `MultifieldParser` to search both `title` and `content` fields
   - Parses keyword query using Whoosh's query syntax
   - Supports AND/OR operators, wildcards, phrases

2. **Tag Search** (`search.py:107-110`):
   - Uses `QueryParser` specifically for the `tags` field
   - Tag queries are exact matches (Whoosh KEYWORD field)

3. **Query Combination** (`search.py:112-123`):
   - If both keyword and tag provided: Uses `And` query (intersection)
   - If only one: Uses that query directly
   - If none: Returns all notes (`Every` query)

**Result Scoring**:
- Whoosh uses **BM25F** (Best Match 25 with field weighting) for relevance scoring
- Scores are returned in `SearchResult.score`
- After Whoosh retrieval, scores are re-ranked by a backlink popularity boost

**Backlink Boost** (`search.py:95-109`):

By default, BM25F scores are multiplied by a popularity factor based on how many other notes link to each result:

```
boost = 1 + log(backlink_count + 1)
combined_score = bm25f_score × boost
```

- Notes with 0 backlinks get boost = 1.0 (score unchanged)
- Notes with 9 backlinks get boost ≈ 2.3×
- Notes with 99 backlinks get boost ≈ 5.6×
- Disable with `--no-boost` flag

**Outgoing Links**:
- Each `SearchResult` includes `outgoing_links`: the top 5 wikilinks found in the note content
- Extracted by `extract_top_wikilinks()` from `wikilinks.py` (order-preserving)

**Date Filtering**:
- `after` filter: Excludes notes created before the date
- `before` filter: Excludes notes created after the date

**Highlights**:
- `hit.highlights("content")` extracts text snippets around matches
- Returns HTML-formatted snippets with `<b>` tags around matching terms

### Tag Listing

**Functions**: 
- `list_all_tags(vault_index)` (`search.py:160-176`)
- `list_tags_by_folder(vault_index, vault_path)` (`search.py:179-224`)

**Algorithm**:
1. Iterate through all notes in the vault
2. For each tag in each note, increment count
3. Return dictionary sorted by count descending

---

## Index Persistence

### Vault Index

**File**: `.kai/vault_index.json`

Contains all note metadata loaded at startup for fast access.

### Search Index

**Directory**: `.kai/whoosh_index/`

Whoosh index files for full-text search. Rebuilt on `rebuild-index`.

### Cache

**Directory**: `.kai/cache/`

Caches external data like YouTube transcripts to avoid re-fetching.

---

## Algorithm Complexity

| Operation | Time Complexity | Space Complexity |
|-----------|-----------------|------------------|
| Search (BM25F, Whoosh) | O(log N + M) | O(N × avg_doc_size) |
| Backlink boost | O(N) | O(N) |
| Wikilink extraction | O(L) per note | O(W) |
| Concept linking TF-IDF Build | O(N × L × F) | O(N × V) |
| Concept linking similarity | O(N × V + N²) | O(N × V) |
| Tag similarity | O(T²) | O(T) |
| Co-occurrence | O(T² × N) | O(T × N) |

Where:
- N = number of notes
- M = number of matches
- F = max features (TF-IDF)
- L = average document length
- K = max features per folder
- V = vocabulary size
- T = number of unique tags
- W = number of wikilinks in document
