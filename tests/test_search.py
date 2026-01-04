"""Tests for search functionality."""

from datetime import datetime
from pathlib import Path

import pytest

from obsidian_ai_tools.indexer import NoteMetadata, VaultIndex, build_index
from obsidian_ai_tools.search import (
    SearchQuery,
    list_all_tags,
    list_tags_by_folder,
    search_notes,
)


class TestListAllTags:
    """Tests for list_all_tags function."""

    def test_list_tags_empty_vault(self) -> None:
        """Test listing tags from empty vault."""
        index = VaultIndex(notes=[], index_path=Path("/tmp/index.json"))

        tags = list_all_tags(index)

        assert tags == {}

    def test_list_tags_with_notes(self) -> None:
        """Test listing tags with counts."""
        notes = [
            NoteMetadata(
                file_path=Path("/test/note1.md"),
                title="Note 1",
                tags=["ai", "python"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 1",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=Path("/test/note2.md"),
                title="Note 2",
                tags=["ai", "llm"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 2",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=Path("/test/note3.md"),
                title="Note 3",
                tags=["python"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 3",
                modified_time=1234567890.0,
            ),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        tags = list_all_tags(index)

        assert tags == {"ai": 2, "python": 2, "llm": 1}

    def test_list_tags_sorted_by_count(self) -> None:
        """Test that tags are sorted by count descending."""
        notes = [
            NoteMetadata(
                file_path=Path(f"/test/note{i}.md"),
                title=f"Note {i}",
                tags=["popular"] if i < 3 else ["rare"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content=f"Content {i}",
                modified_time=1234567890.0,
            )
            for i in range(5)
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        tags = list_all_tags(index)
        tag_list = list(tags.keys())

        # Most popular should be first
        assert tag_list[0] == "popular"
        assert tags["popular"] == 3
        assert tags["rare"] == 2


class TestListTagsByFolder:
    """Tests for list_tags_by_folder function."""

    def test_list_tags_by_folder_empty_vault(self) -> None:
        """Test listing tags by folder from empty vault."""
        index = VaultIndex(notes=[], index_path=Path("/tmp/index.json"))
        vault_path = Path("/vault")

        result = list_tags_by_folder(index, vault_path)

        assert result == {}

    def test_list_tags_by_folder_single_folder(self) -> None:
        """Test listing tags with all notes in one folder."""
        vault_path = Path("/vault")
        notes = [
            NoteMetadata(
                file_path=vault_path / "inbox" / "note1.md",
                title="Note 1",
                tags=["ai", "python"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 1",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "inbox" / "note2.md",
                title="Note 2",
                tags=["ai", "llm"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 2",
                modified_time=1234567890.0,
            ),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)

        assert "inbox" in result
        assert result["inbox"] == {"ai": 2, "llm": 1, "python": 1}

    def test_list_tags_by_folder_multiple_folders(self) -> None:
        """Test listing tags across multiple folders."""
        vault_path = Path("/vault")
        notes = [
            NoteMetadata(
                file_path=vault_path / "inbox" / "note1.md",
                title="Note 1",
                tags=["ai", "research"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 1",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "projects" / "ml" / "note2.md",
                title="Note 2",
                tags=["ai", "python", "llm"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 2",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "archive" / "note3.md",
                title="Note 3",
                tags=["testing"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 3",
                modified_time=1234567890.0,
            ),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)

        # Should have 3 folders
        assert len(result) == 3
        assert "inbox" in result
        assert "projects/ml" in result
        assert "archive" in result

        # Check tag counts per folder
        assert result["inbox"] == {"ai": 1, "research": 1}
        assert result["projects/ml"] == {"ai": 1, "python": 1, "llm": 1}
        assert result["archive"] == {"testing": 1}

    def test_list_tags_by_folder_same_tag_in_multiple_folders(self) -> None:
        """Test that the same tag can appear in multiple folders with different counts."""
        vault_path = Path("/vault")
        notes = [
            NoteMetadata(
                file_path=vault_path / "inbox" / "note1.md",
                title="Note 1",
                tags=["ai"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 1",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "inbox" / "note2.md",
                title="Note 2",
                tags=["ai"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 2",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "projects" / "note3.md",
                title="Note 3",
                tags=["ai"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content 3",
                modified_time=1234567890.0,
            ),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)

        # Same tag 'ai' should appear in both folders independently
        assert result["inbox"] == {"ai": 2}
        assert result["projects"] == {"ai": 1}

    def test_list_tags_by_folder_root_notes(self) -> None:
        """Test handling notes in vault root directory."""
        vault_path = Path("/vault")
        notes = [
            NoteMetadata(
                file_path=vault_path / "root_note.md",
                title="Root Note",
                tags=["root-tag"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "inbox" / "note.md",
                title="Inbox Note",
                tags=["inbox-tag"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content",
                modified_time=1234567890.0,
            ),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)

        # Root notes should be grouped under "(root)"
        assert "(root)" in result
        assert result["(root)"] == {"root-tag": 1}
        assert result["inbox"] == {"inbox-tag": 1}

    def test_list_tags_by_folder_sorted_alphabetically(self) -> None:
        """Test that folders are sorted alphabetically."""
        vault_path = Path("/vault")
        notes = [
            NoteMetadata(
                file_path=vault_path / "zebra" / "note.md",
                title="Note",
                tags=["tag"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "alpha" / "note.md",
                title="Note",
                tags=["tag"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "beta" / "note.md",
                title="Note",
                tags=["tag"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content",
                modified_time=1234567890.0,
            ),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)
        folder_list = list(result.keys())

        # Folders should be alphabetically sorted
        assert folder_list == ["alpha", "beta", "zebra"]

    def test_list_tags_by_folder_tags_sorted_by_count(self) -> None:
        """Test that tags within each folder are sorted by count descending."""
        vault_path = Path("/vault")
        notes = [
            NoteMetadata(
                file_path=vault_path / "inbox" / f"note{i}.md",
                title=f"Note {i}",
                tags=["popular"] if i < 3 else ["rare"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content=f"Content {i}",
                modified_time=1234567890.0,
            )
            for i in range(5)
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)
        tag_list = list(result["inbox"].keys())

        # Most popular tag should be first
        assert tag_list[0] == "popular"
        assert result["inbox"]["popular"] == 3
        assert result["inbox"]["rare"] == 2

    def test_list_tags_by_folder_notes_without_tags(self) -> None:
        """Test that notes without tags don't create empty folder entries."""
        vault_path = Path("/vault")
        notes = [
            NoteMetadata(
                file_path=vault_path / "inbox" / "note1.md",
                title="Note with tags",
                tags=["ai"],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content",
                modified_time=1234567890.0,
            ),
            NoteMetadata(
                file_path=vault_path / "empty" / "note2.md",
                title="Note without tags",
                tags=[],
                created=None,
                author=None,
                source_url=None,
                source_type=None,
                content="Content",
                modified_time=1234567890.0,
            ),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)

        # Only inbox should appear (has tags), empty should not
        assert "inbox" in result
        assert "empty" not in result


class TestSearchNotes:
    """Tests for search_notes function."""

    @pytest.fixture
    def test_vault(self, tmp_path: Path) -> tuple[VaultIndex, Path]:
        """Create  test vault with sample notes."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Note 1: AI and Python
        (inbox / "note1.md").write_text(
            """---
title: Introduction to AI
tags:
  - ai
  - python
created: 2026-01-01T10:00:00
author: John Doe
---

This is about artificial intelligence and machine learning using python."""
        )

        # Note 2: Python only
        (inbox / "note2.md").write_text(
            """---
title: Python Basics
tags:
  - python
created: 2026-01-02T10:00:00
---

Learn Python programming fundamentals."""
        )

        # Note 3: No tags
        (inbox / "note3.md").write_text(
            """---
title: Random Thoughts
created: 2026-01-03T10:00:00
---

Just some random content here."""
        )

        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / ".kai" / "whoosh_index"

        return vault_index, index_dir

    def test_search_by_keyword(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test keyword search."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", limit=10)
        results = search_notes(query, vault_index, index_dir)

        # Should find notes 1 and 2
        assert len(results) == 2
        titles = [r.note.title for r in results]
        assert "Introduction to AI" in titles
        assert "Python Basics" in titles

    def test_search_by_tag(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test tag search."""
        vault_index, index_dir = test_vault

        query = SearchQuery(tag="ai", limit=10)
        results = search_notes(query, vault_index, index_dir)

        # Should find only note 1
        assert len(results) == 1
        assert results[0].note.title == "Introduction to AI"

    def test_search_by_date_range(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test date range filtering."""
        vault_index, index_dir = test_vault

        # Search after Jan 1
        query = SearchQuery(
            keyword="",
            after=datetime(2026, 1, 2),
            limit=10,
        )
        results = search_notes(query, vault_index, index_dir)

        # Should find notes 2 and 3
        assert len(results) == 2
        titles = [r.note.title for r in results]
        assert "Python Basics" in titles
        assert "Random Thoughts" in titles

    def test_search_combined_filters(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test combining keyword and tag filters."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", tag="ai", limit=10)
        results = search_notes(query, vault_index, index_dir)

        # Should find only note 1 (has both python content and ai tag)
        assert len(results) == 1
        assert results[0].note.title == "Introduction to AI"

    def test_search_with_limit(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test result limit."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="", limit=2)
        results = search_notes(query, vault_index, index_dir)

        # Should return at most 2 results
        assert len(results) <= 2

    def test_search_returns_scores(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test that results include relevance scores."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", limit=10)
        results = search_notes(query, vault_index, index_dir)

        # All results should have scores
        for result in results:
            assert result.score > 0


class TestSearchQuery:
    """Tests for SearchQuery model."""

    def test_search_query_defaults(self) -> None:
        """Test SearchQuery default values."""
        query = SearchQuery()

        assert query.keyword is None
        assert query.tag is None
        assert query.after is None
        assert query.before is None
        assert query.limit == 10

    def test_search_query_custom_values(self) -> None:
        """Test SearchQuery with custom values."""
        query = SearchQuery(
            keyword="test",
            tag="ai",
            after=datetime(2026, 1, 1),
            before=datetime(2026, 1, 31),
            limit=20,
        )

        assert query.keyword == "test"
        assert query.tag == "ai"
        assert query.after == datetime(2026, 1, 1)
        assert query.before == datetime(2026, 1, 31)
        assert query.limit == 20
