# Search and Analysis Algorithms

This document describes the algorithms, indexes, and methods used by the `kai` CLI commands for search, concept linking, and tag management.

## Table of Contents

1. [kai search](#kai-search)
2. [kai overview](#kai-overview)
3. [kai follow](#kai-follow)
4. [kai connect](#kai-connect)
5. [kai tags](#kai-tags)

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

## kai overview

The `kai overview` command generates a vault terrain map using **inter-folder TF-IDF** to extract distinctive keywords per folder.

### Algorithm

**Module**: `overview.py` — `_build_overview(vault_index, top_n)`

**Corpus construction**:
- One document per folder: concatenate all note titles and content from that folder
- IDF is computed across folders (not across individual notes)
- This means terms common across many folders (e.g., "summary", "source") are suppressed; terms distinctive to a specific folder score higher

**TF-IDF Vectorization** (`overview.py:_extract_folder_keywords`):
```python
TfidfVectorizer(
    max_features=200,
    stop_words="english",
    ngram_range=(1, 2),
)
```

**Single-folder fallback**: When the vault has only one folder, IDF is undefined (all terms have equal IDF). In this case, the vectorizer falls back to per-document TF scoring within that folder's content.

**Output per folder** (`FolderSummary`):
- `folder`: Relative path from vault root
- `note_count`: Number of notes in folder
- `top_keywords`: Top-N distinctive keywords (inter-folder TF-IDF)
- `top_tags`: Tags with counts, sorted by frequency

**Output formats**:
- `terminal` — Human-readable aligned table
- `markdown` — Markdown document
- `json` — Full `VaultOverview` model serialized
- `compact` — One line per folder, pipe-delimited (for agent system prompt injection)

---

## kai follow

The `kai follow` command resolves a wikilink target and displays the raw note content with its outgoing links.

### Wikilink Resolution

**Module**: `wikilinks.py` — `resolve_wikilink(target, vault_index)`

Resolution order (first match wins):
1. **Exact title match** (case-insensitive): `note.title.lower() == target.lower()`
2. **Filename stem match** (case-insensitive): `note.file_path.stem.lower() == target.lower()`

If no match is found, an error is shown.

### Wikilink Extraction

**Module**: `wikilinks.py` — shared utilities

```python
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
```

- `extract_wikilinks(content)` → `set[str]` — deduplicated set, alias stripped
- `extract_top_wikilinks(content, n=5)` → `list[str]` — first N in document order, alias stripped
- `count_backlinks(vault_index)` → `dict[str, int]` — keys are lowercased note targets

The shared `wikilinks.py` module is used by `search.py` (outgoing links), `digest.py` (backlink counts), `concept_linking.py`, and `cli.py` (`follow` command).

---

## kai connect

The `kai connect` command discovers connections between notes using **TF-IDF similarity** and keyword analysis.

### Concept Linker Class

**Class**: `ConceptLinker` (`concept_linking.py:107-466`)

### TF-IDF Index Building

**Function**: `build_tfidf_index()` (`concept_linking.py:121-150`)

1. **Document Preparation**:
   - Combines `title + " " + content` for each note
   - Stores note paths in order for index mapping

2. **TF-IDF Vectorization** (`concept_linking.py:141-149`):
   ```python
   TfidfVectorizer(
       max_features=5000,      # Max vocabulary size
       stop_words="english",   # Remove common words
       ngram_range=(1, 2),     # Unigrams + Bigrams
       min_df=1,               # Min document frequency
       max_df=0.95,            # Max document frequency (remove too common)
   )
   ```

3. **Output**: Sparse matrix of shape `(num_notes, max_features)`

### Similarity Calculation

**Function**: `find_similar(note_path, top_n, threshold)` (`concept_linking.py:152-237`)

1. **Find Source Index**: Locate the note's row in the TF-IDF matrix
2. **Cosine Similarity**: Compute similarity between source and all other notes
   ```python
   similarities = cosine_similarity(source_vector, tfidf_matrix).flatten()
   ```
3. **Filter by Threshold**: Keep only notes with similarity ≥ threshold (default 0.3)
4. **Extract Shared Keywords**: Find overlapping TF-IDF features
5. **Exclude Already Linked**: Skip notes already referenced via `[[wikilinks]]`
6. **Sort & Limit**: Return top-N suggestions sorted by score

### Keyword Extraction

**Function**: `_extract_shared_keywords(source_idx, target_idx, top_n)` (`concept_linking.py:317-352`)

1. Get non-zero TF-IDF features for both notes
2. Compute intersection of feature indices
3. Sort shared features by combined TF-IDF weight
4. Return top-N feature names as keywords

### Pairwise Connection Discovery

**Function**: `find_all_connections(threshold)` (`concept_linking.py:239-315`)

1. Computes full pairwise similarity matrix
2. For each unique pair (i, j) where i < j:
   - Checks similarity score
   - Skips if already linked
   - Extracts shared keywords
3. Returns all connections above threshold

### Orphan Detection

**Function**: `find_orphans()` (`concept_linking.py:354-402`)

1. Build map of all note titles (normalized)
2. Scan all notes for `[[wikilink]]` patterns
3. Track incoming links for each note
4. Orphan = note with no incoming AND no outgoing links

### Wikilink Insertion

**Function**: `insert_wikilinks(note_path, suggestions, dry_run)` (`concept_linking.py:404-466`)

1. Appends "## Related Notes" section to note
2. For each suggestion, creates: `[[relative-path|note-title]]`
3. If dry_run=True, returns changes without writing

---

## kai tags

The `kai tags` command analyzes and manages tag hygiene using multiple algorithms.

### Tag Similarity Detection

**Function**: `calculate_similarity(tag_a, tag_b)` (`tag_hygiene.py:119-129`)

Uses Python's `difflib.SequenceMatcher` for character-based similarity:
```python
return SequenceMatcher(None, tag_a.lower(), tag_b.lower()).ratio()
```

### Semantic Root Checking

**Function**: `_tags_share_semantic_root(tag_a, tag_b)` (`tag_hygiene.py:156-223`)

Prevents false positives by:

1. **Compound Tags**: For tags with same word count (e.g., `X-development`), requires first word to match
2. **Stem Extraction** (`_get_word_stems`): Extracts word stems (first 5+ chars of each word)
3. **Prefix Matching**: For simple tags, requires 5+ character shared prefix

**Examples**:
- ✅ `neurodivergent` vs `neurodivergence` → True (shared prefix "neuro")
- ✅ `socio-technical` vs `sociotechnical` → True (stem overlap)
- ❌ `ai-development` vs `ui-development` → False (different first words)

### Similar Tag Grouping

**Function**: `find_similar_tags(vault_index, threshold)` (`tag_hygiene.py:226-321`)

**Algorithm**:
1. Build tag → note count mapping
2. For each tag pair:
   - Skip prefix matches (e.g., "ai" vs "ai-development")
   - Check character similarity ≥ threshold (default 0.8)
   - Verify semantic root match
3. Group similar tags, select canonical as most-used variant
4. Sort groups by total note count

### Co-occurrence Analysis

**Function**: `analyze_cooccurrence(vault_index, min_overlap, min_jaccard)` (`tag_hygiene.py:324-383`)

**Jaccard Similarity**:
```
J(A, B) = |A ∩ B| / |A ∪ B|
```

Where:
- A = set of notes with tag A
- B = set of notes with tag B
- Intersection = notes with both tags
- Union = notes with either tag

**Process**:
1. Build tag → set of notes mapping
2. For each tag pair:
   - Calculate overlap count
   - Skip if < min_overlap (default 3)
   - Calculate Jaccard similarity
   - Skip if < min_jaccard (default 0.5)
3. Sort by co-occurrence count descending

### Orphan Tag Detection

**Function**: `find_orphan_tags(vault_index)` (`tag_hygiene.py:386-410`)

1. Count tag occurrences across all notes
2. Identify tags with count == 1
3. Return OrphanTag objects with note path

### Tag Consolidation

**Function**: `apply_consolidation(note_path, consolidation)` (`tag_hygiene.py:491-549`)

1. Load note with python-frontmatter
2. Remove old tags from frontmatter
3. Add canonical tag (for merge action)
4. Create backup before writing
5. Write updated frontmatter

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
| Overview TF-IDF (folders) | O(F × L × K) | O(F × V) |
| Wikilink extraction | O(L) per note | O(W) |
| Concept linking TF-IDF Build | O(N × L × F) | O(N × V) |
| Concept linking similarity | O(N × V + N²) | O(N × V) |
| Tag similarity | O(T²) | O(T) |
| Co-occurrence | O(T² × N) | O(T × N) |

Where:
- N = number of notes
- M = number of matches
- F = number of folders (overview) or max features (TF-IDF)
- L = average document length
- K = max features per folder
- V = vocabulary size
- T = number of unique tags
- W = number of wikilinks in document
