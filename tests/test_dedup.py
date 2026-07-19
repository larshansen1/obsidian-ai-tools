"""Tests for source-based duplicate detection."""

from pathlib import Path

import pytest

from obsidian_ai_tools.dedup import (
    ExistingNote,
    find_note_by_source,
    normalize_source_url,
)


def _write_note_file(path: Path, source_url: str, title: str = "Existing Note") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
title: {title}
tags:
  - test
  - vault
created: 2026-07-19T10:00:00
type: source-note
source_type: web
source_url: {source_url}
model: test-model
prompt_version: article_v1
---

# {title}

Body content.
""",
        encoding="utf-8",
    )


class TestNormalizeSourceUrl:
    def test_strips_tracking_params(self) -> None:
        assert normalize_source_url(
            "https://example.com/post?utm_source=x&utm_medium=y&fbclid=abc&id=7"
        ) == normalize_source_url("https://example.com/post?id=7")

    def test_ignores_fragment_and_trailing_slash(self) -> None:
        assert normalize_source_url("https://example.com/post/#section") == normalize_source_url(
            "http://www.example.com/post"
        )

    def test_sorts_remaining_query_params(self) -> None:
        assert normalize_source_url("https://example.com/p?b=2&a=1") == normalize_source_url(
            "https://example.com/p?a=1&b=2"
        )

    @pytest.mark.parametrize(
        "variant",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
            "https://youtu.be/dQw4w9WgXcQ?si=share123",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
        ],
    )
    def test_youtube_variants_collapse(self, variant: str) -> None:
        assert normalize_source_url(variant) == "youtube.com/watch?v=dQw4w9WgXcQ"

    def test_different_videos_stay_distinct(self) -> None:
        assert normalize_source_url("https://youtu.be/aaa") != normalize_source_url(
            "https://youtu.be/bbb"
        )

    def test_non_http_sources_only_trimmed(self) -> None:
        assert normalize_source_url(" ./documents/paper.pdf ") == "./documents/paper.pdf"


class TestFindNoteBySource:
    def test_finds_exact_match(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "web-note.md", "https://example.com/article")

        found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found == ExistingNote(
            file_path=tmp_path / "inbox" / "web-note.md",
            title="Existing Note",
            tags=["test", "vault"],
            source_type="web",
        )

    def test_finds_normalized_match(self, tmp_path: Path) -> None:
        _write_note_file(
            tmp_path / "inbox" / "yt-note.md", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )

        found = find_note_by_source(tmp_path, "https://youtu.be/dQw4w9WgXcQ?si=xyz")

        assert found is not None
        assert found.file_path == tmp_path / "inbox" / "yt-note.md"

    def test_returns_none_without_match(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "web-note.md", "https://example.com/other")

        assert find_note_by_source(tmp_path, "https://example.com/article") is None

    def test_ignores_hidden_directories(self, tmp_path: Path) -> None:
        _write_note_file(
            tmp_path / ".obsidian" / "cache" / "note.md", "https://example.com/article"
        )

        assert find_note_by_source(tmp_path, "https://example.com/article") is None

    def test_ignores_notes_without_frontmatter(self, tmp_path: Path) -> None:
        plain = tmp_path / "inbox" / "plain.md"
        plain.parent.mkdir(parents=True)
        plain.write_text("# Just a heading\n\nhttps://example.com/article\n", encoding="utf-8")

        assert find_note_by_source(tmp_path, "https://example.com/article") is None

    def test_empty_vault(self, tmp_path: Path) -> None:
        assert find_note_by_source(tmp_path, "https://example.com/article") is None
