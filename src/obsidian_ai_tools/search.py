"""Search functionality for Obsidian vault using Whoosh."""

import math
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from whoosh import index
from whoosh.fields import DATETIME, ID, KEYWORD, TEXT, Schema
from whoosh.qparser import MultifieldParser, QueryParser
from whoosh.query import And, Every

from .indexer import NoteMetadata, VaultIndex
from .wikilinks import extract_top_wikilinks


class SearchResult(BaseModel):
    """A single search result."""

    note: NoteMetadata = Field(..., description="Note metadata")
    score: float = Field(..., description="Relevance score")
    highlights: str | None = Field(None, description="Highlighted snippet")
    explanation: str | None = Field(None, description="Result explanation")
    outgoing_links: list[str] = Field(default_factory=list, description="Top outgoing wikilinks")


class SearchQuery(BaseModel):
    """Search query parameters."""

    keyword: str | None = Field(default=None, description="Keyword to search for")
    tag: str | None = Field(default=None, description="Tag to filter by")
    after: datetime | None = Field(default=None, description="Created after this date")
    before: datetime | None = Field(default=None, description="Created before this date")
    limit: int = Field(default=10, description="Maximum number of results")
    explain: bool = Field(default=False, description="Include result explanations")
    no_boost: bool = Field(default=False, description="Disable backlink score boosting")


def get_whoosh_schema() -> Schema:
    """Define Whoosh schema for note indexing."""
    return Schema(
        file_path=ID(stored=True, unique=True),
        title=TEXT(stored=True),
        content=TEXT(stored=True),
        tags=KEYWORD(stored=True, commas=True, scorable=True),
        author=TEXT(stored=True),
        source_url=ID(stored=True),
        created=DATETIME(stored=True),
    )


def build_whoosh_index(vault_index: VaultIndex, index_dir: Path) -> None:
    """Build Whoosh search index from vault index.

    Args:
        vault_index: VaultIndex with note metadata
        index_dir: Directory to store Whoosh index
    """
    index_dir.mkdir(parents=True, exist_ok=True)

    # Always recreate index to avoid duplicates
    ix = index.create_in(str(index_dir), get_whoosh_schema())

    writer = ix.writer()

    for note in vault_index.notes:
        writer.add_document(
            file_path=str(note.file_path),
            title=note.title,
            content=note.content,
            tags=",".join(note.tags),
            author=note.author or "",
            source_url=note.source_url or "",
            created=note.created,
        )

    writer.commit()


def search_notes(
    query: SearchQuery,
    vault_index: VaultIndex,
    index_dir: Path,
    backlinks: dict[str, int] | None = None,
) -> list[SearchResult]:
    """Search notes using Whoosh BM25F with optional backlink boosting."""
    notes_by_path = {note.file_path: note for note in vault_index.notes}
    results = _search_whoosh(query, vault_index, index_dir, query.limit, notes_by_path)
    if not query.no_boost and backlinks:
        results = _apply_backlink_boost(results, backlinks)
    return results


def _apply_backlink_boost(
    results: list[SearchResult],
    backlinks: dict[str, int],
) -> list[SearchResult]:
    """Re-score results by multiplying BM25F score with a backlink popularity factor.

    boost = 1 + log(backlink_count + 1)
    Combined score = bm25_score * boost
    """
    boosted: list[SearchResult] = []
    for result in results:
        count = backlinks.get(result.note.title.lower(), 0)
        boost = 1.0 + math.log(count + 1)
        boosted.append(result.model_copy(update={"score": result.score * boost}))
    return sorted(boosted, key=lambda r: r.score, reverse=True)


def _search_whoosh(
    query: SearchQuery,
    vault_index: VaultIndex,
    index_dir: Path,
    limit: int,
    notes_by_path: dict[Path, NoteMetadata],
) -> list[SearchResult]:
    if not index.exists_in(str(index_dir)):
        build_whoosh_index(vault_index, index_dir)

    ix = index.open_dir(str(index_dir))

    with ix.searcher() as searcher:
        query_parts = []

        if query.keyword:
            parser = MultifieldParser(["title", "content"], schema=ix.schema)
            keyword_query = parser.parse(query.keyword)
            query_parts.append(keyword_query)

        if query.tag:
            tag_parser = QueryParser("tags", schema=ix.schema)
            tag_query = tag_parser.parse(query.tag)
            query_parts.append(tag_query)

        if not query_parts:
            combined_query = Every()
        elif len(query_parts) == 1:
            combined_query = query_parts[0]
        else:
            combined_query = And(query_parts)

        results = searcher.search(combined_query, limit=limit)
        search_results: list[SearchResult] = []

        for hit in results:
            file_path = Path(hit["file_path"])
            note = notes_by_path.get(file_path)

            if not note or not _note_matches_filters(note, query):
                continue

            highlights = hit.highlights("content")
            explanation = _build_explanation(note, query, reason="keyword match")
            search_results.append(
                SearchResult(
                    note=note,
                    score=float(hit.score or 0.0),
                    highlights=highlights if highlights else None,
                    explanation=explanation,
                    outgoing_links=extract_top_wikilinks(note.content),
                )
            )

        return search_results


def _note_matches_filters(note: NoteMetadata, query: SearchQuery) -> bool:
    if query.tag and query.tag not in note.tags:
        return False
    if query.after and note.created and note.created < query.after:
        return False
    if query.before and note.created and note.created > query.before:
        return False
    return True


def _build_explanation(note: NoteMetadata, query: SearchQuery, reason: str) -> str | None:
    if not query.explain:
        return None

    tags = ", ".join(note.tags[:5]) if note.tags else "none"
    parts = [f"Reason: {reason}", f"tags: {tags}"]

    if query.keyword:
        keywords = _extract_keywords(query.keyword)
        if keywords:
            parts.append(f"keywords: {', '.join(keywords)}")

    return "; ".join(parts)


def _extract_keywords(keyword: str) -> list[str]:
    terms = re.findall(r"[\w-]+", keyword.lower())
    unique_terms: list[str] = []
    for term in terms:
        if term not in unique_terms:
            unique_terms.append(term)
        if len(unique_terms) >= 5:
            break
    return unique_terms


def list_all_tags(vault_index: VaultIndex) -> dict[str, int]:
    """List all tags with their counts.

    Args:
        vault_index: VaultIndex with note metadata

    Returns:
        Dictionary mapping tag to count
    """
    tag_counts: dict[str, int] = {}

    for note in vault_index.notes:
        for tag in note.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True))
