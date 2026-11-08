"""Tests for source-based duplicate detection."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_ai_tools.dedup import (
    ExistingNote,
    SourceIndex,
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

    @pytest.mark.parametrize(
        "variant",
        [
            "https://www.x.com/user/status/123",
            "https://x.com/user/status/123?s=20",
            "https://twitter.com/user/status/123",
            "https://www.twitter.com/user/status/123?t=abc123",
            "https://twitter.com/user/status/123?s=20&t=abc123&utm_source=x",
        ],
    )
    def test_x_variants_collapse(self, variant: str) -> None:
        """All X/twitter forms of the same status canonicalize to x.com."""
        assert normalize_source_url(variant) == "x.com/user/status/123"

    def test_tracking_params_stripped_only_on_x_hosts(self) -> None:
        """s/t are X noise but meaningful query params on other sites."""
        assert normalize_source_url("https://example.com/p?s=20&t=42") == (
            "example.com/p?s=20&t=42"
        )


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
            "---\ntitle: T\nsource_url: https://example.com/article\n---\nbody\n",
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

        assert found is None

    def test_non_dict_yaml_frontmatter_still_found(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "n.md", "https://example.com/article")
        with patch("obsidian_ai_tools._vault_store.yaml.safe_load", return_value=[1, 2]):
            found = find_note_by_source(tmp_path, "https://example.com/article")

        assert found is None

    def test_parse_frontmatter_uses_utf8(self, tmp_path: Path) -> None:
        """The frontmatter parser reads with explicit encoding."""
        from obsidian_ai_tools._vault_store import VaultStore

        note = tmp_path / "inbox" / "n.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\ntitle: x\n---\n", encoding="utf-8")

        with patch("pathlib.Path.read_text", return_value="---\ntitle: x\n---\n"):
            fm, content = VaultStore.parse_frontmatter(note)

        assert fm == {"title": "x"}


def _set_mtime(path: Path, mtime_ns: int) -> None:
    """Pin a file's mtime so change detection never depends on clock resolution."""
    os.utime(path, ns=(mtime_ns, mtime_ns))


class TestSourceIndex:
    """The cached lookup used by the extension badge."""

    def test_finds_match_like_find_note_by_source(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "web-note.md", "https://example.com/article")

        found = SourceIndex().find(tmp_path, "https://www.example.com/article/?utm_source=x")

        assert found == ExistingNote(
            file_path=tmp_path / "inbox" / "web-note.md",
            title="Existing Note",
            tags=["test", "vault"],
            source_type="web",
        )

    def test_returns_none_without_match(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "inbox" / "web-note.md", "https://example.com/other")

        assert SourceIndex().find(tmp_path, "https://example.com/article") is None

    def test_first_match_in_sorted_order_wins(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "b.md", "https://example.com/article", title="B")
        _write_note_file(tmp_path / "a.md", "https://example.com/article", title="A")

        found = SourceIndex().find(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.title == "A"

    def test_unchanged_notes_are_not_reparsed(self, tmp_path: Path) -> None:
        note = tmp_path / "inbox" / "web-note.md"
        _write_note_file(note, "https://example.com/article")
        _set_mtime(note, 1_000_000_000)
        index = SourceIndex()
        index.find(tmp_path, "https://example.com/missing")

        with patch("obsidian_ai_tools.dedup._parse_frontmatter") as mock_parse:
            found = index.find(tmp_path, "https://example.com/article")

        mock_parse.assert_not_called()
        assert found is not None
        assert found.file_path == note

    def test_edited_note_is_reread(self, tmp_path: Path) -> None:
        note = tmp_path / "inbox" / "web-note.md"
        _write_note_file(note, "https://example.com/old")
        _set_mtime(note, 1_000_000_000)
        index = SourceIndex()
        assert index.find(tmp_path, "https://example.com/new") is None

        _write_note_file(note, "https://example.com/new")
        _set_mtime(note, 1_000_000_001)

        found = index.find(tmp_path, "https://example.com/new")
        assert found is not None
        assert found.file_path == note
        assert index.find(tmp_path, "https://example.com/old") is None

    def test_new_note_is_found_on_next_lookup(self, tmp_path: Path) -> None:
        index = SourceIndex()
        assert index.find(tmp_path, "https://example.com/article") is None

        _write_note_file(tmp_path / "inbox" / "web-note.md", "https://example.com/article")

        found = index.find(tmp_path, "https://example.com/article")
        assert found is not None
        assert found.file_path == tmp_path / "inbox" / "web-note.md"

    def test_deleted_note_is_forgotten(self, tmp_path: Path) -> None:
        note = tmp_path / "inbox" / "web-note.md"
        _write_note_file(note, "https://example.com/article")
        index = SourceIndex()
        assert index.find(tmp_path, "https://example.com/article") is not None

        note.unlink()

        assert index.find(tmp_path, "https://example.com/article") is None

    def test_note_without_source_url_never_matches(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain.md"
        plain.write_text("---\ntitle: Plain\n---\nbody\n", encoding="utf-8")

        assert SourceIndex().find(tmp_path, "https://example.com/article") is None

    def test_file_deleted_during_walk_is_skipped(self, tmp_path: Path) -> None:
        _write_note_file(tmp_path / "a.md", "https://example.com/gone")
        _write_note_file(tmp_path / "b.md", "https://example.com/article")
        real_stat = Path.stat

        def flaky_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
            if self.name == "a.md":
                raise FileNotFoundError(self)
            return real_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", flaky_stat):
            found = SourceIndex().find(tmp_path, "https://example.com/article")

        assert found is not None
        assert found.file_path == tmp_path / "b.md"

    def test_vaults_are_indexed_separately(self, tmp_path: Path) -> None:
        vault_a = tmp_path / "a"
        vault_b = tmp_path / "b"
        _write_note_file(vault_a / "note.md", "https://example.com/article")
        vault_b.mkdir()
        index = SourceIndex()

        assert index.find(vault_a, "https://example.com/article") is not None
        assert index.find(vault_b, "https://example.com/article") is None
        assert index.find(vault_a, "https://example.com/article") is not None
