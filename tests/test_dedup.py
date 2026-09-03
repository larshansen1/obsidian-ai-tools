"""Tests for source-based duplicate detection."""

from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_ai_tools.dedup import (
    ExistingNote,
    _read_frontmatter_block,
    _youtube_video_id,
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


class TestYoutubeVideoId:
    """Edge cases of the private _youtube_video_id helper."""

    def test_youtu_be_with_extra_path_segments(self) -> None:
        """youtu.be ids come from the first path segment only."""
        assert _youtube_video_id("youtu.be", "/abc/def", []) == "abc"

    def test_youtu_be_strips_only_slashes(self) -> None:
        """Only '/' characters are stripped from the youtu.be path."""
        assert _youtube_video_id("youtu.be", "/XabcX/", []) == "XabcX"

    def test_watch_with_trailing_slash_uses_query(self) -> None:
        assert _youtube_video_id("youtube.com", "/watch/", [("v", "dQw4w9WgXcQ")]) == (
            "dQw4w9WgXcQ"
        )

    def test_two_segment_path_requires_known_prefix(self) -> None:
        assert _youtube_video_id("youtube.com", "/foo/bar", []) is None

    def test_live_paths(self) -> None:
        assert _youtube_video_id("youtube.com", "/live/dQw4w9WgXcQ", []) == "dQw4w9WgXcQ"

    def test_v_paths(self) -> None:
        assert _youtube_video_id("youtube.com", "/v/dQw4w9WgXcQ", []) == "dQw4w9WgXcQ"


class TestNormalizeSourceUrlPorts:
    """Default HTTP(S) ports are stripped from hosts."""

    def test_port_443_stripped(self) -> None:
        assert normalize_source_url("https://example.com:443/p") == "example.com/p"

    def test_port_80_stripped(self) -> None:
        assert normalize_source_url("https://example.com:80/p") == "example.com/p"


class TestNormalizeSourceUrlQueryEdgeCases:
    """Query handling beyond the tracking-parameter tests."""

    def test_blank_query_values_kept(self) -> None:
        assert normalize_source_url("https://example.com/p?a=&b=1") == "example.com/p?a=&b=1"

    def test_no_query_no_question_mark(self) -> None:
        assert normalize_source_url("https://example.com/p") == "example.com/p"

    def test_blank_query_value_urlencoded(self) -> None:
        assert normalize_source_url("https://example.com/p?a=&b=1&c=2") == (
            "example.com/p?a=&b=1&c=2"
        )


class TestNormalizeSourceUrlPathEdgeCases:
    """Path normalization edge cases."""

    def test_trailing_x_is_data_not_separator(self) -> None:
        """Only '/' is stripped from paths; other characters are content."""
        assert normalize_source_url("https://example.com/postX") == "example.com/postX"

    def test_query_preserved_verbatim(self) -> None:
        assert normalize_source_url("https://example.com/p?a=1&b=2") == "example.com/p?a=1&b=2"


class TestFindNoteBySourceAdditional:
    """More find_note_by_source scan-order and parse-failure paths."""

    def test_quoted_source_url(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "quoted.md", '"https://example.com/article"')

        found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.file_path == tmp_path / "inbox" / "quoted.md"

    def test_source_url_without_space_after_colon(self, tmp_path: Path) -> None:
        note = tmp_path / "inbox" / "nospace.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntitle: T\nsource_url:https://example.com/article\n---\nbody\n",
            encoding="utf-8",
        )

        found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is not None

    def test_source_url_trailing_x_preserved(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "x.md", "https://example.com/articleX")

        found = find_note_by_source(tmp_path, "https://example.com/articleX")

        assert found is not None
        assert found.file_path == tmp_path / "inbox" / "x.md"

    def test_first_note_without_frontmatter_does_not_abort_scan(self, tmp_path: Path) -> None:
        plain = tmp_path / "inbox" / "00-plain.md"
        plain.parent.mkdir(parents=True)
        plain.write_text("# hello\n", encoding="utf-8")
        _write_note_file(tmp_path / "inbox" / "note.md", "https://example.com/article")

        found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.file_path == tmp_path / "inbox" / "note.md"

    def test_nonmatching_first_note_does_not_abort_scan(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "00-other.md", "https://example.com/other")
        _write_note_file(tmp_path / "inbox" / "note.md", "https://example.com/article")

        found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.file_path == tmp_path / "inbox" / "note.md"

    def test_hidden_dir_first_does_not_abort_scan(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / ".obsidian" / "x.md", "https://example.com/article")
        _write_note_file(tmp_path / "inbox" / "note.md", "https://example.com/article")

        found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.file_path == tmp_path / "inbox" / "note.md"

    def test_invalid_yaml_frontmatter_still_found(self, tmp_path: Path) -> None:
        note = tmp_path / "inbox" / "broken.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntitle: Broken\nsource_url: https://example.com/article\ntags: [unclosed\n"
            "---\nbody\n",
            encoding="utf-8",
        )

        found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.tags == []

    def test_non_dict_yaml_frontmatter_still_found(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "n.md", "https://example.com/article")
        with patch("obsidian_ai_tools.dedup.yaml.safe_load", return_value=[1, 2]):
            found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.tags == []

    def test_read_frontmatter_block_uses_utf8(self, tmp_path: Path) -> None:
        """The frontmatter read pins encoding='utf-8' explicitly."""
        note = tmp_path / "inbox" / "n.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\ntitle: x\n---\n", encoding="utf-8")
        import io

        with patch("obsidian_ai_tools.dedup.Path.open") as mock_open:
            mock_open.return_value.__enter__.return_value = io.StringIO("---\ntitle: x\n---\n")
            result = _read_frontmatter_block(note)

        assert result == "title: x\n"
        assert mock_open.call_args.kwargs["encoding"] == "utf-8"
