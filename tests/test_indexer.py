"""Tests for vault indexing functionality."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_ai_tools.indexer import (
    NoteMetadata,
    VaultIndex,
    build_index,
    parse_frontmatter,
    scan_vault,
)


class TestParseFrontmatter:
    """Tests for parse_frontmatter function."""

    def test_parse_valid_frontmatter(self, tmp_path: Path) -> None:
        """Test parsing valid frontmatter."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            """---
title: Test Note
tags:
  - test
  - example
---

# Content

This is the content."""
        )

        result = parse_frontmatter(test_file)

        assert result["frontmatter"]["title"] == "Test Note"
        assert result["frontmatter"]["tags"] == ["test", "example"]
        assert "# Content" in result["content"]

    def test_parse_no_frontmatter(self, tmp_path: Path) -> None:
        """Test parsing file without frontmatter."""
        test_file = tmp_path / "test.md"
        test_file.write_text("# Just content\n\nNo frontmatter here.")

        result = parse_frontmatter(test_file)

        assert result["frontmatter"] == {}
        assert "# Just content" in result["content"]

    def test_parse_invalid_file_raises_error(self) -> None:
        """Test that parsing non-existent file raises error."""
        with pytest.raises(Exception, match="Failed to parse frontmatter"):
            parse_frontmatter(Path("/nonexistent/file.md"))


class TestScanVault:
    """Tests for scan_vault function."""

    def test_scan_empty_vault(self, tmp_path: Path) -> None:
        """Test scanning empty vault."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        notes = scan_vault(tmp_path, "inbox")

        assert notes == []

    def test_scan_vault_with_notes(self, tmp_path: Path) -> None:
        """Test scanning vault with valid notes."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Create test note
        note1 = inbox / "note1.md"
        note1.write_text(
            """---
title: Test Note 1
tags:
  - test
created: 2026-01-01T10:00:00
author: Test Author
---

Content here."""
        )

        notes = scan_vault(tmp_path, "inbox")

        assert len(notes) == 1
        assert notes[0].title == "Test Note 1"
        assert notes[0].tags == ["test"]
        assert notes[0].author == "Test Author"
        assert notes[0].created == datetime(2026, 1, 1, 10, 0, 0)

    def test_scan_vault_missing_folder(self, tmp_path: Path) -> None:
        """Test scanning non-existent folder."""
        notes = scan_vault(tmp_path, "nonexistent")

        assert notes == []

    def test_scan_vault_skips_malformed_files(self, tmp_path: Path) -> None:
        """Test that malformed files are skipped."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Create valid note
        valid = inbox / "valid.md"
        valid.write_text("---\ntitle: Valid\n---\nContent")

        # Create invalid note (will fail to parse)
        invalid = inbox / "invalid.md"
        # Write binary data that can't be decoded
        invalid.write_bytes(b"\x80\x81\x82")

        notes = scan_vault(tmp_path, "inbox")

        # Should only get the valid note
        assert len(notes) == 1
        assert notes[0].title == "Valid"

    def test_scan_vault_uses_filename_as_fallback_title(self, tmp_path: Path) -> None:
        """Test that filename is used when title is missing."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        note = inbox / "my-note.md"
        note.write_text("---\ntags:\n  - test\n---\nContent")

        notes = scan_vault(tmp_path, "inbox")

        assert len(notes) == 1
        assert notes[0].title == "my-note"

    def test_default_folder_scans_inbox(self, tmp_path: Path) -> None:
        """The default folder parameter is "inbox"."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "n.md").write_text("---\ntitle: N\n---\nbody\n")

        notes = scan_vault(tmp_path)

        assert len(notes) == 1
        assert notes[0].title == "N"

    def test_extracts_source_url_and_source_type(self, tmp_path: Path) -> None:
        """source_url and source_type frontmatter keys reach the metadata."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "n.md").write_text(
            "---\ntitle: N\nsource_url: https://example.com/art\nsource_type: web\n---\nbody\n"
        )

        notes = scan_vault(tmp_path, "inbox")

        assert len(notes) == 1
        assert notes[0].source_url == "https://example.com/art"
        assert notes[0].source_type == "web"

    def test_warning_logged_with_file_path_and_exc_info(self, tmp_path: Path) -> None:
        """Malformed files log the path and full exc_info."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        bad_file = inbox / "bad.md"
        bad_file.write_bytes(b"\x80\x81\x82")

        with patch("obsidian_ai_tools.indexer.logging.getLogger") as mock_gl:
            scan_vault(tmp_path, "inbox")

        assert mock_gl.call_args.args[0] == "obsidian_ai_tools.indexer"
        warn = mock_gl.return_value.warning
        assert warn.call_count >= 1
        call = warn.call_args
        assert call.args[0] == "Skipping note that could not be indexed: %s"
        assert call.args[1] == bad_file
        assert call.kwargs.get("exc_info") is True


class TestVaultIndex:
    """Tests for VaultIndex class."""

    def test_save_and_load_index(self, tmp_path: Path) -> None:
        """Test saving and loading vault index."""
        index_path = tmp_path / "index.json"

        # Create index with test data
        note = NoteMetadata(
            file_path=Path("/test/note.md"),
            title="Test",
            tags=["test"],
            created=datetime(2026, 1, 1, 10, 0, 0),
            author="Author",
            source_url="https://example.com",
            source_type="youtube",
            content="Content here",
            modified_time=1234567890.0,
        )

        index = VaultIndex(notes=[note], index_path=index_path)
        index.save()

        # Load index
        loaded = VaultIndex.load(index_path)

        assert loaded is not None
        assert len(loaded.notes) == 1
        assert loaded.notes[0].title == "Test"
        assert loaded.notes[0].tags == ["test"]
        assert loaded.notes[0].author == "Author"

    def test_load_nonexistent_index(self, tmp_path: Path) -> None:
        """Test loading non-existent index returns None."""
        index_path = tmp_path / "nonexistent.json"

        result = VaultIndex.load(index_path)

        assert result is None

    def test_load_corrupted_index(self, tmp_path: Path) -> None:
        """Test loading corrupted index returns None."""
        index_path = tmp_path / "corrupted.json"
        index_path.write_text("{ corrupted json")

        result = VaultIndex.load(index_path)

        assert result is None

    def test_save_creates_nested_parent_directories(self, tmp_path: Path) -> None:
        """save() creates any missing parent directories of the index path."""
        index_path = tmp_path / "deep" / "nested" / "index.json"
        note = NoteMetadata(
            file_path=Path("/test/note.md"),
            title="Test",
            content="Content",
            modified_time=123.0,
        )

        VaultIndex(notes=[note], index_path=index_path).save()

        assert index_path.exists()
        reloaded = json.loads(index_path.read_text())
        assert reloaded["notes"][0]["title"] == "Test"

    def test_save_writes_pretty_json_with_two_space_indent(self, tmp_path: Path) -> None:
        """save() pretty-prints the JSON with a 2-space indent."""
        index_path = tmp_path / "index.json"
        note = NoteMetadata(
            file_path=Path("/test/note.md"),
            title="Test",
            content="Content",
            modified_time=123.0,
        )

        VaultIndex(notes=[note], index_path=index_path).save()

        lines = index_path.read_text().splitlines()
        assert len(lines) > 1  # pretty-printed, not a single JSON blob
        assert any(line == '  "notes": [' for line in lines)

    def test_load_round_trips_created_timestamp(self, tmp_path: Path) -> None:
        """load() converts the created string back into a datetime."""
        index_path = tmp_path / "index.json"
        note = NoteMetadata(
            file_path=Path("/test/note.md"),
            title="Test",
            created=datetime(2026, 1, 1, 10, 0, 0),
            content="Content",
            modified_time=123.0,
        )

        VaultIndex(notes=[note], index_path=index_path).save()
        loaded = VaultIndex.load(index_path)

        assert loaded is not None
        assert loaded.notes[0].created == datetime(2026, 1, 1, 10, 0, 0)
        assert loaded.notes[0].file_path == Path("/test/note.md")


class TestBuildIndex:
    """Tests for build_index function."""

    def test_build_index_creates_new_index(self, tmp_path: Path) -> None:
        """Test building new index."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        note = inbox / "test.md"
        note.write_text("---\ntitle: Test\n---\nContent")

        index = build_index(tmp_path, "inbox")

        assert len(index.notes) == 1
        assert index.notes[0].title == "Test"

    def test_build_index_uses_cached_index(self, tmp_path: Path) -> None:
        """Test that cached index is used when vault hasn't changed."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        note = inbox / "test.md"
        note.write_text("---\ntitle: Original\n---\nContent")

        # Build initial index
        index1 = build_index(tmp_path, "inbox")
        index1_time = index1.last_updated

        # Build again without changes - should use cache
        index2 = build_index(tmp_path, "inbox")

        # Should be the same instance from cache
        assert index2.last_updated == index1_time

    def test_build_index_rebuilds_on_file_change(self, tmp_path: Path) -> None:
        """Test that index is rebuilt when files change."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        note = inbox / "test.md"
        note.write_text("---\ntitle: Original\n---\nContent")

        # Build initial index
        _ = build_index(tmp_path, "inbox")

        # Modify file (touch it to update modification time)
        import time

        time.sleep(0.1)  # Ensure different timestamp
        note.write_text("---\ntitle: Modified\n---\nNew content")

        # Build again - should rebuild
        index2 = build_index(tmp_path, "inbox")

        assert index2.notes[0].title == "Modified"

    def test_build_index_force_rebuild(self, tmp_path: Path) -> None:
        """Test forcing index rebuild."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        note = inbox / "test.md"
        note.write_text("---\ntitle: Test\n---\nContent")

        # Build initial index
        index1 = build_index(tmp_path, "inbox")
        time1 = index1.last_updated

        # Force rebuild
        import time

        time.sleep(0.1)
        index2 = build_index(tmp_path, "inbox", force_rebuild=True)

        # Should have different timestamp
        assert index2.last_updated > time1

    def _write_cached_index(self, index_path: Path, last_updated: str) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps({"notes": [], "last_updated": last_updated}), encoding="utf-8"
        )

    def test_default_folder_builds_inbox(self, tmp_path: Path) -> None:
        """build_index() with no folder indexes the inbox subfolder."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "n.md").write_text("---\ntitle: N\n---\nbody\n")

        index = build_index(tmp_path)

        assert len(index.notes) == 1
        assert index.notes[0].title == "N"

    def test_folder_none_cached_rebuild_is_stable(self, tmp_path: Path) -> None:
        """A second folder=None build reuses the cache when nothing changed."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "a.md").write_text("---\ntitle: A\n---\nx\n")

        index1 = build_index(tmp_path, folder=None)
        index2 = build_index(tmp_path, folder=None)

        assert index2.last_updated == index1.last_updated

    def test_folder_none_rebuild_picks_up_new_file(self, tmp_path: Path) -> None:
        """folder=None rebuilds when a new note appears anywhere in the vault."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "a.md").write_text("---\ntitle: A\n---\nx\n")
        build_index(tmp_path, folder=None)

        (inbox / "b.md").write_text("---\ntitle: New Note\n---\nxx\n")
        # Filesystem timestamp granularity can make a freshly written file look
        # older than the index build, so pin its mtime strictly in the future.
        future = time.time() + 10
        os.utime(inbox / "b.md", (future, future))
        index2 = build_index(tmp_path, folder=None)

        assert "New Note" in {n.title for n in index2.notes}

    def test_empty_scan_folder_uses_zero_default_mtime(self, tmp_path: Path) -> None:
        """An empty scan folder defaults latest_mtime to 0 and keeps the cache."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        index_path = tmp_path / ".kai" / "vault_index.json"
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        self._write_cached_index(index_path, "1970-01-01T00:00:00+00:00")

        result = build_index(tmp_path, "inbox", index_path=index_path)

        assert result.last_updated == epoch

    def test_rebuild_when_mtime_equals_index_timestamp(self, tmp_path: Path) -> None:
        """latest_mtime <= index_timestamp keeps the cache at exact equality."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        note = inbox / "a.md"
        note.write_text("---\ntitle: A\n---\nx\n")

        boundary = datetime(2026, 1, 1)
        os.utime(note, (boundary.timestamp(), boundary.timestamp()))
        index_path = tmp_path / ".kai" / "vault_index.json"
        self._write_cached_index(index_path, "2026-01-01T00:00:00")

        result = build_index(tmp_path, "inbox", index_path=index_path)

        assert result.last_updated == boundary

    def test_folder_inbox_ignores_root_level_notes(self, tmp_path: Path) -> None:
        """folder='inbox' scans only the inbox folder, not the vault root."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "a.md").write_text("---\ntitle: A\n---\nx\n")
        (tmp_path / "root.md").write_text("---\ntitle: Root\n---\nx\n")

        index = build_index(tmp_path, "inbox")

        assert len(index.notes) == 1
        assert index.notes[0].title == "A"
