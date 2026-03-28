"""Tests for search functionality."""

import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from obsidian_ai_tools.indexer import NoteMetadata, VaultIndex, build_index
from obsidian_ai_tools.search import (
    SearchQuery,
    SearchResult,
    _apply_backlink_boost,
    list_all_tags,
    list_tags_by_folder,
    search_notes,
)


def _make_note(
    path: Path,
    title: str,
    tags: list[str] | None = None,
    content: str = "content",
    created: datetime | None = None,
) -> NoteMetadata:
    return NoteMetadata(
        file_path=path,
        title=title,
        tags=tags or [],
        created=created,
        author=None,
        source_url=None,
        source_type=None,
        content=content,
        modified_time=1234567890.0,
    )


class TestListAllTags:
    """Tests for list_all_tags function."""

    def test_list_tags_empty_vault(self) -> None:
        """Test listing tags from empty vault."""
        index = VaultIndex(notes=[], index_path=Path("/tmp/index.json"))

        tags = list_all_tags(index)

        assert tags == {}

    def test_list_tags_with_notes(self) -> None:
        """Test listing tags from vault with notes."""
        notes = [
            _make_note(Path("/vault/note1.md"), "Note 1", ["ai", "python"]),
            _make_note(Path("/vault/note2.md"), "Note 2", ["python"]),
            _make_note(Path("/vault/note3.md"), "Note 3", ["ai"]),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        tags = list_all_tags(index)

        assert tags["python"] == 2
        assert tags["ai"] == 2

    def test_list_tags_sorted_by_count(self) -> None:
        """Test that tags are sorted by count descending."""
        notes = [
            _make_note(Path("/vault/note1.md"), "Note 1", ["popular", "rare"]),
            _make_note(Path("/vault/note2.md"), "Note 2", ["popular"]),
            _make_note(Path("/vault/note3.md"), "Note 3", ["popular"]),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        tags = list_all_tags(index)
        tag_list = list(tags.keys())

        assert tag_list[0] == "popular"


class TestListTagsByFolder:
    """Tests for list_tags_by_folder function."""

    def test_list_tags_by_folder_empty_vault(self) -> None:
        """Test listing tags by folder from empty vault."""
        index = VaultIndex(notes=[], index_path=Path("/tmp/index.json"))
        vault_path = Path("/vault")

        result = list_tags_by_folder(index, vault_path)

        assert result == {}

    def test_list_tags_by_folder_groups_correctly(self) -> None:
        """Test that notes are correctly grouped by folder."""
        vault_path = Path("/vault")
        notes = [
            _make_note(vault_path / "inbox" / "note1.md", "Note 1", ["ai"]),
            _make_note(vault_path / "projects" / "note2.md", "Note 2", ["python"]),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)

        assert "inbox" in result
        assert "projects" in result
        assert result["inbox"]["ai"] == 1
        assert result["projects"]["python"] == 1

    def test_list_tags_by_folder_sorted_alphabetically(self) -> None:
        """Test that folders are sorted alphabetically."""
        vault_path = Path("/vault")
        notes = [
            _make_note(vault_path / "zebra" / "n.md", "N", ["t"]),
            _make_note(vault_path / "alpha" / "n.md", "N", ["t"]),
            _make_note(vault_path / "beta" / "n.md", "N", ["t"]),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)
        folder_list = list(result.keys())

        assert folder_list == ["alpha", "beta", "zebra"]

    def test_list_tags_by_folder_tags_sorted_by_count(self) -> None:
        """Test that tags within each folder are sorted by count descending."""
        vault_path = Path("/vault")
        notes = [
            _make_note(
                vault_path / "inbox" / f"note{i}.md",
                f"Note {i}",
                ["popular"] if i < 3 else ["rare"],
            )
            for i in range(5)
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)
        tag_list = list(result["inbox"].keys())

        assert tag_list[0] == "popular"
        assert result["inbox"]["popular"] == 3
        assert result["inbox"]["rare"] == 2

    def test_list_tags_by_folder_notes_without_tags(self) -> None:
        """Test that notes without tags don't create empty folder entries."""
        vault_path = Path("/vault")
        notes = [
            _make_note(vault_path / "inbox" / "note1.md", "Note with tags", ["ai"]),
            _make_note(vault_path / "empty" / "note2.md", "Note without tags", []),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/tmp/index.json"))

        result = list_tags_by_folder(index, vault_path)

        assert "inbox" in result
        assert "empty" not in result


class TestSearchNotes:
    """Tests for search_notes function."""

    @pytest.fixture
    def test_vault(self, tmp_path: Path) -> tuple[VaultIndex, Path]:
        """Create test vault with sample notes."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        (inbox / "note1.md").write_text(
            textwrap.dedent(
                """
                ---
                title: Introduction to AI
                tags:
                  - ai
                  - python
                created: 2026-01-01T10:00:00
                author: John Doe
                ---

                This is about artificial intelligence and machine learning using python.
                """
            ).lstrip()
        )

        (inbox / "note2.md").write_text(
            textwrap.dedent(
                """
                ---
                title: Python Basics
                tags:
                  - python
                created: 2026-01-02T10:00:00
                ---

                Learn Python programming fundamentals.
                """
            ).lstrip()
        )

        (inbox / "note3.md").write_text(
            textwrap.dedent(
                """
                ---
                title: Random Thoughts
                created: 2026-01-03T10:00:00
                ---

                Just some random content here.
                """
            ).lstrip()
        )

        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / ".kai" / "whoosh_index"

        return vault_index, index_dir

    def test_search_by_keyword(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test keyword search."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", limit=10)
        results = search_notes(query, vault_index, index_dir)

        assert len(results) == 2
        titles = [r.note.title for r in results]
        assert "Introduction to AI" in titles
        assert "Python Basics" in titles

    def test_search_by_tag(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test tag search."""
        vault_index, index_dir = test_vault

        query = SearchQuery(tag="ai", limit=10)
        results = search_notes(query, vault_index, index_dir)

        assert len(results) == 1
        assert results[0].note.title == "Introduction to AI"

    def test_search_by_date_range(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test date range filtering."""
        vault_index, index_dir = test_vault

        query = SearchQuery(
            keyword="",
            after=datetime(2026, 1, 2),
            limit=10,
        )
        results = search_notes(query, vault_index, index_dir)

        assert len(results) == 2
        titles = [r.note.title for r in results]
        assert "Python Basics" in titles
        assert "Random Thoughts" in titles

    def test_search_combined_filters(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test combining keyword and tag filters."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", tag="ai", limit=10)
        results = search_notes(query, vault_index, index_dir)

        assert len(results) == 1
        assert results[0].note.title == "Introduction to AI"

    def test_search_with_limit(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test result limit."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="", limit=2)
        results = search_notes(query, vault_index, index_dir)

        assert len(results) <= 2

    def test_search_returns_scores(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Test that results include relevance scores."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", limit=10)
        results = search_notes(query, vault_index, index_dir)

        for result in results:
            assert result.score > 0

    def test_search_explain_adds_reason(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Explain flag adds reason lines for keyword search."""
        vault_index, index_dir = test_vault

        keyword_query = SearchQuery(keyword="python", limit=10, explain=True)
        keyword_results = search_notes(keyword_query, vault_index, index_dir)

        assert keyword_results[0].explanation
        assert "Reason: keyword match" in keyword_results[0].explanation
        assert "keywords:" in keyword_results[0].explanation

    def test_search_returns_outgoing_links(self, test_vault: tuple[VaultIndex, Path]) -> None:
        """Search results include outgoing wikilinks field."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", limit=10)
        results = search_notes(query, vault_index, index_dir)

        # outgoing_links should always be present (empty list is fine)
        for result in results:
            assert isinstance(result.outgoing_links, list)


class TestBacklinkBoost:
    """Tests for backlink score boosting."""

    def _make_result(self, title: str, score: float) -> SearchResult:
        note = NoteMetadata(
            file_path=Path(f"/vault/{title}.md"),
            title=title,
            tags=[],
            created=None,
            author=None,
            source_url=None,
            source_type=None,
            content="",
            modified_time=0.0,
        )
        return SearchResult(note=note, score=score)

    def test_zero_backlinks_neutral_boost(self) -> None:
        """A note with 0 backlinks gets boost = 1.0 (score unchanged)."""
        results = [self._make_result("Lonely Note", 1.0)]
        backlinks: dict[str, int] = {}
        boosted = _apply_backlink_boost(results, backlinks)
        assert boosted[0].score == pytest.approx(1.0)

    def test_boost_increases_score(self) -> None:
        """A note with backlinks scores higher after boost (keys are lowercase)."""
        results = [self._make_result("Popular Note", 1.0)]
        backlinks = {"popular note": 9}  # keys are lowercased by count_backlinks
        boosted = _apply_backlink_boost(results, backlinks)
        assert boosted[0].score > 1.0

    def test_boost_reorders_results(self) -> None:
        """A less-scoring but highly linked note can overtake a higher-scoring one."""
        results = [
            self._make_result("High BM25", 2.0),
            self._make_result("Linked Note", 1.0),
        ]
        backlinks = {"linked note": 99}  # keys are lowercased by count_backlinks
        boosted = _apply_backlink_boost(results, backlinks)
        assert boosted[0].note.title == "Linked Note"

    def test_no_boost_flag_disables_reranking(self, tmp_path: Path) -> None:
        """no_boost=True skips _apply_backlink_boost; scores equal raw BM25F."""
        from obsidian_ai_tools.wikilinks import count_backlinks

        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Alpha has 1 backlink (from Beta). Beta has 0 backlinks.
        # Without boost, BM25 order may put Beta first (it has more "python" content).
        # With boost, Alpha should score higher due to its backlink.
        (inbox / "a.md").write_text("---\ntitle: Alpha\n---\npython basics\n", encoding="utf-8")
        (inbox / "b.md").write_text(
            "---\ntitle: Beta\n---\npython advanced python tutorial [[Alpha]]\n",
            encoding="utf-8",
        )

        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / ".kai" / "whoosh_index"
        backlinks = count_backlinks(vault_index)

        # With no_boost: backlinks dict is ignored, scores are pure BM25F
        query_no_boost = SearchQuery(keyword="python", limit=10, no_boost=True)
        results_no_boost = search_notes(query_no_boost, vault_index, index_dir, backlinks=backlinks)

        # With boost: Alpha's score gets multiplied by (1 + log(2)) ≈ 1.69
        query_boost = SearchQuery(keyword="python", limit=10, no_boost=False)
        results_boost = search_notes(query_boost, vault_index, index_dir, backlinks=backlinks)

        assert len(results_no_boost) == 2
        assert len(results_boost) == 2

        # no_boost scores should equal raw BM25F (not multiplied by backlink factor)
        scores_no_boost = {r.note.title: r.score for r in results_no_boost}
        scores_boost = {r.note.title: r.score for r in results_boost}

        # Alpha has a backlink so its boosted score must be strictly higher
        assert scores_boost["Alpha"] > scores_no_boost["Alpha"]
        # Beta has no backlinks so its scores are identical (boost = 1.0)
        assert scores_boost["Beta"] == pytest.approx(scores_no_boost["Beta"])


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
        assert query.explain is False
        assert query.no_boost is False

    def test_search_query_custom_values(self) -> None:
        """Test SearchQuery with custom values."""
        query = SearchQuery(
            keyword="test",
            tag="ai",
            after=datetime(2026, 1, 1),
            before=datetime(2026, 1, 31),
            limit=20,
            explain=True,
            no_boost=True,
        )

        assert query.keyword == "test"
        assert query.tag == "ai"
        assert query.after == datetime(2026, 1, 1)
        assert query.before == datetime(2026, 1, 31)
        assert query.limit == 20
        assert query.explain is True
        assert query.no_boost is True
