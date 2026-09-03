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
    _build_explanation,
    _extract_keywords,
    _note_matches_filters,
    build_whoosh_index,
    get_whoosh_schema,
    list_all_tags,
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

    def test_search_highlights_include_matched_terms(
        self, test_vault: tuple[VaultIndex, Path]
    ) -> None:
        """Keyword search results carry a non-empty highlighted snippet."""
        vault_index, index_dir = test_vault

        query = SearchQuery(keyword="python", limit=10)
        results = search_notes(query, vault_index, index_dir)

        assert any(r.highlights is not None and "python" in r.highlights for r in results)

    def test_search_carries_outgoing_links_from_content(self, tmp_path: Path) -> None:
        """outgoing_links reflect the note's own [[wikilinks]]."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "b.md").write_text("---\ntitle: Beta\n---\npython advanced python [[Gamma]]\n")

        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / ".kai" / "whoosh_index"
        results = search_notes(SearchQuery(keyword="python"), vault_index, index_dir)

        assert len(results) == 1
        assert results[0].outgoing_links == ["Gamma"]

    def test_scores_below_one_for_single_term(self, tmp_path: Path) -> None:
        """BM25F scores for a single term stay in (0, 1)."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "one.md").write_text("---\ntitle: One\n---\npython\n")

        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / ".kai" / "whoosh_index"
        results = search_notes(SearchQuery(keyword="python"), vault_index, index_dir)

        assert len(results) == 1
        assert 0.0 < results[0].score < 1.0

    def test_filtered_high_ranked_note_does_not_truncate_results(self, tmp_path: Path) -> None:
        """A filtered-out top hit must not silently drop later matches.

        "B" has the highest BM25F score but is excluded by the date filter;
        the loop over hits must continue and still return A and C.
        """
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "a.md").write_text(
            "---\ntitle: A\ncreated: 2026-02-01T10:00:00\ntags: [ai]\n---\npython intro\n"
        )
        (inbox / "b.md").write_text(
            "---\ntitle: B\ncreated: 2025-01-01T10:00:00\n---\npython python python deep\n"
        )
        (inbox / "c.md").write_text(
            "---\ntitle: C\ncreated: 2026-02-02T10:00:00\ntags: [ai]\n---\npython guide\n"
        )

        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / ".kai" / "whoosh_index"
        results = search_notes(
            SearchQuery(keyword="python", after=datetime(2026, 1, 1), limit=10),
            vault_index,
            index_dir,
        )

        assert {r.note.title for r in results} == {"A", "C"}


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


class TestWhooshSchema:
    """Tests for the Whoosh index schema field options."""

    def test_schema_contains_all_fields(self) -> None:
        """Schema exposes exactly the six searchable note fields."""
        schema = get_whoosh_schema()
        assert set(schema.names()) == {
            "file_path",
            "title",
            "content",
            "tags",
            "author",
            "source_url",
            "created",
        }

    def test_file_path_stored_and_unique(self) -> None:
        """file_path is the unique stored document identifier."""
        schema = get_whoosh_schema()
        assert schema["file_path"].stored is True
        assert schema["file_path"].unique is True

    def test_text_fields_stored(self) -> None:
        """Title, author and content are stored for result display."""
        schema = get_whoosh_schema()
        assert schema["title"].stored is True
        assert schema["content"].stored is True
        assert schema["author"].stored is True

    def test_tags_keyword_flags(self) -> None:
        """tags is a stored, scorable comma-separated keyword field."""
        schema = get_whoosh_schema()
        assert schema["tags"].stored is True
        assert schema["tags"].scorable is True

    def test_source_url_and_created_stored(self) -> None:
        """source_url and created are stored so metadata survives indexing."""
        schema = get_whoosh_schema()
        assert schema["source_url"].stored is True
        assert schema["created"].stored is True


class TestBuildWhooshIndex:
    """Tests for build_whoosh_index stored document contents."""

    def test_stored_fields_round_trip(self, tmp_path: Path) -> None:
        """Document fields land in the index exactly as provided."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "note.md").write_text(
            "---\ntitle: Stored Note\ncreated: 2026-03-01T10:00:00\n"
            "source_url: https://example.com/source\nauthor: Jane Doe\n---\npython content here\n"
        )
        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / "nested" / "whoosh_index"

        build_whoosh_index(vault_index, index_dir)

        from whoosh import index

        with index.open_dir(str(index_dir)).searcher() as searcher:
            doc = searcher.document(file_path=str(inbox / "note.md"))
            assert doc["title"] == "Stored Note"
            assert doc["author"] == "Jane Doe"
            assert doc["source_url"] == "https://example.com/source"
            assert doc["created"] == datetime(2026, 3, 1, 10, 0, 0)

    def test_missing_optional_fields_stored_empty(self, tmp_path: Path) -> None:
        """Notes without author/source_url store the empty string, not junk."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "bare.md").write_text("---\ntitle: Bare\n---\npython content here\n")
        vault_index = build_index(tmp_path, "inbox")
        index_dir = tmp_path / ".kai" / "whoosh_index"

        build_whoosh_index(vault_index, index_dir)

        from whoosh import index

        with index.open_dir(str(index_dir)).searcher() as searcher:
            doc = searcher.document(file_path=str(inbox / "bare.md"))
            assert doc["author"] == ""
            assert doc["source_url"] == ""


class TestNoteMatchesFilters:
    """Unit tests for _note_matches_filters."""

    def _note(self, tags: list[str] | None = None, created: datetime | None = None) -> NoteMetadata:
        return NoteMetadata(
            file_path=Path("/vault/n.md"),
            title="T",
            tags=tags or [],
            created=created,
            content="",
            modified_time=0.0,
        )

    def test_no_filters_accepts(self) -> None:
        assert _note_matches_filters(self._note(), SearchQuery()) is True

    def test_matching_tag_accepts(self) -> None:
        note = self._note(tags=["ai", "python"])
        assert _note_matches_filters(note, SearchQuery(tag="ai")) is True

    def test_mismatched_tag_rejected(self) -> None:
        note = self._note(tags=["ai"])
        assert _note_matches_filters(note, SearchQuery(tag="zzz")) is False

    def test_created_before_after_rejected(self) -> None:
        note = self._note(created=datetime(2025, 1, 1))
        assert _note_matches_filters(note, SearchQuery(after=datetime(2026, 1, 1))) is False

    def test_created_after_before_rejected(self) -> None:
        note = self._note(created=datetime(2027, 1, 1))
        assert _note_matches_filters(note, SearchQuery(before=datetime(2026, 1, 1))) is False

    def test_created_equal_to_after_accepted(self) -> None:
        """A note created exactly at the after boundary is kept (<, not <=)."""
        note = self._note(created=datetime(2026, 1, 1, 10, 0, 0))
        assert (
            _note_matches_filters(note, SearchQuery(after=datetime(2026, 1, 1, 10, 0, 0))) is True
        )

    def test_created_equal_to_before_accepted(self) -> None:
        """A note created exactly at the before boundary is kept (>, not >=)."""
        note = self._note(created=datetime(2026, 1, 1, 10, 0, 0))
        assert (
            _note_matches_filters(note, SearchQuery(before=datetime(2026, 1, 1, 10, 0, 0))) is True
        )

    def test_created_none_ignores_date_filters(self) -> None:
        note = self._note(created=None)
        query = SearchQuery(after=datetime(2026, 1, 1), before=datetime(2026, 2, 1))
        assert _note_matches_filters(note, query) is True


class TestBuildExplanation:
    """Unit tests for _build_explanation."""

    def _note(self, tags: list[str], content: str = "python basics") -> NoteMetadata:
        return NoteMetadata(
            file_path=Path("/vault/n.md"),
            title="T",
            tags=tags,
            created=None,
            content=content,
            modified_time=0.0,
        )

    def test_not_explained_returns_none(self) -> None:
        note = self._note(["ai"])
        assert _build_explanation(note, SearchQuery(keyword="python"), "keyword match") is None

    def test_reason_tags_and_keywords(self) -> None:
        note = self._note(["ai", "python"])
        query = SearchQuery(keyword="python", explain=True)
        assert (
            _build_explanation(note, query, "keyword match")
            == "Reason: keyword match; tags: ai, python; keywords: python"
        )

    def test_multiple_keywords_joined_with_comma_space(self) -> None:
        """Multiple extracted keywords are joined with ', ' verbatim."""
        note = self._note(["ai"])
        query = SearchQuery(keyword="python ai", explain=True)
        explanation = _build_explanation(note, query, "keyword match")
        assert explanation is not None
        assert "keywords: python, ai" in explanation
        assert "XX, XX" not in explanation

    def test_tags_without_keyword(self) -> None:
        note = self._note(["ai"])
        query = SearchQuery(explain=True)
        assert _build_explanation(note, query, "keyword match") == "Reason: keyword match; tags: ai"

    def test_no_tags_uses_none_placeholder(self) -> None:
        note = self._note([])
        query = SearchQuery(explain=True)
        assert (
            _build_explanation(note, query, "keyword match") == "Reason: keyword match; tags: none"
        )

    def test_tags_truncated_to_five(self) -> None:
        note = self._note([f"t{i}" for i in range(6)])
        query = SearchQuery(explain=True)
        explanation = _build_explanation(note, query, "keyword match")
        assert explanation is not None
        assert "tags: t0, t1, t2, t3, t4" in explanation
        assert "t5" not in explanation

    def test_no_keyword_omits_keywords_line(self) -> None:
        note = self._note(["ai"])
        query = SearchQuery(explain=True)
        explanation = _build_explanation(note, query, "tag match")
        assert explanation is not None
        assert "keywords:" not in explanation


class TestExtractKeywords:
    """Unit tests for _extract_keywords."""

    def test_lowercases_terms(self) -> None:
        assert _extract_keywords("PYTHON Ai") == ["python", "ai"]

    def test_limits_to_five_unique_terms(self) -> None:
        assert _extract_keywords("a b c d e f") == ["a", "b", "c", "d", "e"]

    def test_deduplicates_terms(self) -> None:
        assert _extract_keywords("foo foo bar") == ["foo", "bar"]

    def test_handles_word_dashes_and_underscores(self) -> None:
        """Hyphens and underscores are part of the token itself."""
        assert _extract_keywords("bar-baz _qux") == ["bar-baz", "_qux"]

    def test_empty_string(self) -> None:
        assert _extract_keywords("") == []
