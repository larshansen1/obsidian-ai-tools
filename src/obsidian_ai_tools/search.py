"""Search functionality for Obsidian vault using Whoosh."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from whoosh import index
from whoosh.fields import DATETIME, ID, KEYWORD, TEXT, Schema
from whoosh.qparser import MultifieldParser, QueryParser

from .indexer import NoteMetadata, VaultIndex


class SearchResult(BaseModel):
    """A single search result."""

    note: NoteMetadata = Field(..., description="Note metadata")
    score: float = Field(..., description="Relevance score")
    highlights: str | None = Field(None, description="Highlighted snippet")


class SearchQuery(BaseModel):
    """Search query parameters."""

    keyword: str | None = Field(None, description="Keyword to search for")
    tag: str | None = Field(None, description="Tag to filter by")
    after: datetime | None = Field(None, description="Created after this date")
    before: datetime | None = Field(None, description="Created before this date")
    limit: int = Field(10, description="Maximum number of results")


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
    # Create new index (overwrites existing)
    ix = index.create_in(str(index_dir), get_whoosh_schema())

    # Index all notes
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
) -> list[SearchResult]:
    """Search notes using Whoosh.

    Args:
        query: Search query parameters
        vault_index: VaultIndex for note metadata
        index_dir: Directory with Whoosh index

    Returns:
        List of search results sorted by relevance
    """
    # Ensure Whoosh index exists
    if not index.exists_in(str(index_dir)):
        build_whoosh_index(vault_index, index_dir)

    ix = index.open_dir(str(index_dir))

    # Build query
    with ix.searcher() as searcher:
        query_parts = []

        # Keyword search
        if query.keyword:
            parser = MultifieldParser(["title", "content"], schema=ix.schema)
            keyword_query = parser.parse(query.keyword)
            query_parts.append(keyword_query)

        # Tag search
        if query.tag:
            tag_parser = QueryParser("tags", schema=ix.schema)
            tag_query = tag_parser.parse(query.tag)
            query_parts.append(tag_query)

        # Combine queries
        if not query_parts:
            # No query specified, return all
            from whoosh.query import Every

            combined_query = Every()
        elif len(query_parts) == 1:
            combined_query = query_parts[0]
        else:
            from whoosh.query import And

            combined_query = And(query_parts)

        # Execute search
        results = searcher.search(combined_query, limit=query.limit)

        # Build result list
        search_results = []

        for hit in results:
            # Find corresponding note in vault index
            file_path = Path(hit["file_path"])
            note = next(
                (n for n in vault_index.notes if n.file_path == file_path),
                None,
            )

            if note:
                # Apply date filters
                if query.after and note.created and note.created < query.after:
                    continue
                if query.before and note.created and note.created > query.before:
                    continue

                # Get highlights
                highlights = hit.highlights("content")

                search_results.append(
                    SearchResult(
                        note=note,
                        score=hit.score,
                        highlights=highlights if highlights else None,
                    )
                )

        return search_results


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

    # Sort by count descending
    return dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True))


def list_tags_by_folder(vault_index: VaultIndex, vault_path: Path) -> dict[str, dict[str, int]]:
    """List all tags grouped by folder with counts.

    Args:
        vault_index: VaultIndex with note metadata
        vault_path: Absolute path to the vault root

    Returns:
        Dictionary mapping folder path to tag counts.
        Folder paths are relative to vault root.
        Tags within each folder are sorted by count descending.
    """
    folder_tags: dict[str, dict[str, int]] = {}

    for note in vault_index.notes:
        # Skip notes without tags
        if not note.tags:
            continue

        # Get folder path relative to vault root
        try:
            rel_path = note.file_path.relative_to(vault_path)
            folder = str(rel_path.parent)

            # Normalize root folder to empty string for consistency
            if folder == ".":
                folder = "(root)"
        except ValueError:
            # If file is not relative to vault (shouldn't happen), skip it
            continue

        # Count tags for this folder
        if folder not in folder_tags:
            folder_tags[folder] = {}

        for tag in note.tags:
            folder_tags[folder][tag] = folder_tags[folder].get(tag, 0) + 1

    # Sort folders alphabetically and tags by count within each folder
    sorted_folders = {}
    for folder in sorted(folder_tags.keys()):
        sorted_folders[folder] = dict(
            sorted(folder_tags[folder].items(), key=lambda x: x[1], reverse=True)
        )

    return sorted_folders
