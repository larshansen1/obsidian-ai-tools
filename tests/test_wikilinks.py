"""Tests for wikilinks utility module."""

from datetime import datetime
from pathlib import Path

from obsidian_ai_tools.indexer import NoteMetadata, VaultIndex
from obsidian_ai_tools.wikilinks import (
    count_backlinks,
    extract_top_wikilinks,
    extract_wikilinks,
    resolve_wikilink,
)


def _make_note(path: Path, title: str, content: str = "") -> NoteMetadata:
    return NoteMetadata(
        file_path=path,
        title=title,
        tags=[],
        created=datetime(2026, 1, 1),
        author=None,
        source_url=None,
        source_type=None,
        content=content,
        modified_time=0.0,
    )


SAMPLE_CONTENT = (
    "See [[Python Basics]] and [[AI Overview]] for details. "
    "Also check [[Python Basics|Python]] again and [[Machine Learning]]."
)


class TestExtractWikilinks:
    def test_basic_extraction(self) -> None:
        links = extract_wikilinks(SAMPLE_CONTENT)
        assert "Python Basics" in links
        assert "AI Overview" in links
        assert "Machine Learning" in links

    def test_alias_stripped(self) -> None:
        """[[Link|Alias]] should return 'Link', not 'Alias'."""
        links = extract_wikilinks("See [[Note|Display Text]].")
        assert "Note" in links
        assert "Display Text" not in links

    def test_deduplication(self) -> None:
        """Same link appearing twice should appear once in set."""
        links = extract_wikilinks("[[A]] and [[A]] again")
        assert links == {"A"}

    def test_empty_content(self) -> None:
        assert extract_wikilinks("") == set()

    def test_no_wikilinks(self) -> None:
        assert extract_wikilinks("Just regular text with [markdown](links).") == set()


class TestExtractTopWikilinks:
    def test_preserves_first_occurrence_order(self) -> None:
        content = "[[B]] then [[A]] then [[C]]"
        result = extract_top_wikilinks(content, n=3)
        assert result == ["B", "A", "C"]

    def test_deduplicates_while_preserving_order(self) -> None:
        content = "[[A]] [[B]] [[A]] [[C]]"
        result = extract_top_wikilinks(content, n=10)
        assert result == ["A", "B", "C"]

    def test_respects_n(self) -> None:
        content = "[[A]] [[B]] [[C]] [[D]] [[E]]"
        result = extract_top_wikilinks(content, n=3)
        assert result == ["A", "B", "C"]

    def test_empty_content(self) -> None:
        assert extract_top_wikilinks("") == []

    def test_alias_stripped(self) -> None:
        result = extract_top_wikilinks("[[Note|Alias]]", n=5)
        assert result == ["Note"]


class TestCountBacklinks:
    def test_counts_correctly(self) -> None:
        notes = [
            _make_note(Path("/v/a.md"), "Note A", "Links to [[Note B]] and [[Note C]]"),
            _make_note(Path("/v/b.md"), "Note B", "Links to [[Note C]]"),
            _make_note(Path("/v/c.md"), "Note C", "No outgoing links"),
        ]
        index = VaultIndex(notes=notes, index_path=Path("/v/.kai/index.json"))
        counts = count_backlinks(index)
        # Keys are lowercased
        assert counts["note b"] == 1
        assert counts["note c"] == 2

    def test_empty_vault(self) -> None:
        index = VaultIndex(notes=[], index_path=Path("/v/.kai/index.json"))
        assert count_backlinks(index) == {}

    def test_no_wikilinks(self) -> None:
        notes = [_make_note(Path("/v/a.md"), "Note A", "Plain text, no links.")]
        index = VaultIndex(notes=notes, index_path=Path("/v/.kai/index.json"))
        assert count_backlinks(index) == {}


class TestResolveWikilink:
    def _make_vault(self) -> VaultIndex:
        notes = [
            _make_note(Path("/vault/attention-mechanisms.md"), "Attention Mechanisms"),
            _make_note(Path("/vault/python-basics.md"), "Python Basics"),
            _make_note(Path("/vault/ml-intro.md"), "Introduction to ML"),
        ]
        return VaultIndex(notes=notes, index_path=Path("/vault/.kai/index.json"))

    def test_exact_title_match(self) -> None:
        index = self._make_vault()
        note = resolve_wikilink("Attention Mechanisms", index)
        assert note is not None
        assert note.title == "Attention Mechanisms"

    def test_case_insensitive_title_match(self) -> None:
        index = self._make_vault()
        note = resolve_wikilink("attention mechanisms", index)
        assert note is not None
        assert note.title == "Attention Mechanisms"

    def test_stem_fallback(self) -> None:
        """Matches by filename stem when title doesn't match."""
        index = self._make_vault()
        note = resolve_wikilink("ml-intro", index)
        assert note is not None
        assert note.title == "Introduction to ML"

    def test_title_takes_priority_over_stem(self) -> None:
        """Title match wins over stem match."""
        notes = [
            _make_note(Path("/vault/python-basics.md"), "Python Basics"),
            _make_note(Path("/vault/other.md"), "python-basics"),  # title == stem of first note
        ]
        index = VaultIndex(notes=notes, index_path=Path("/vault/.kai/index.json"))
        note = resolve_wikilink("python-basics", index)
        # Title "python-basics" exact match wins
        assert note is not None
        assert note.title == "python-basics"

    def test_not_found_returns_none(self) -> None:
        index = self._make_vault()
        assert resolve_wikilink("Nonexistent Note", index) is None
