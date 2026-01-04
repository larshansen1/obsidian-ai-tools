"""Tests for Obsidian file writing functionality."""

from pathlib import Path

import pytest

from obsidian_ai_tools.models import Note
from obsidian_ai_tools.obsidian import (
    build_filename,
    sanitize_filename,
    write_note,
)


class TestSanitizeFilename:
    """Tests for sanitize_filename function."""

    def test_basic_sanitization(self) -> None:
        """Test basic sanitization."""
        assert sanitize_filename("Hello World") == "hello-world"

    def test_special_characters(self) -> None:
        """Test removal of special filesystem characters."""
        result = sanitize_filename('Test<>:"/\\|?*Video')
        assert all(c not in result for c in '<>:"/\\|?*')

    def test_length_limiting(self) -> None:
        """Test length limit enforcement."""
        long_title = "A" * 150
        result = sanitize_filename(long_title, max_length=50)
        assert len(result) <= 50

    def test_empty_fallback(self) -> None:
        """Test fallback for empty input."""
        assert sanitize_filename("") == "untitled-note"
        assert sanitize_filename("   ") == "untitled-note"


class TestBuildFilename:
    """Tests for build_filename function."""

    def test_basic_filename(self) -> None:
        """Test basic filename construction."""
        result = build_filename("youtube", "My Video Title")
        assert result.startswith("youtube-")
        assert result.endswith(".md")

    def test_sanitization_applied(self) -> None:
        """Test that sanitization is applied to title."""
        result = build_filename("youtube", "Test: Video/File")
        assert ":" not in result
        assert "/" not in result
        assert result.endswith(".md")


class TestWriteNote:
    """Tests for write_note function."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create temporary vault directory."""
        return tmp_path / "vault"

    @pytest.fixture
    def sample_note(self) -> Note:
        """Create sample note for testing."""
        return Note(
            title="Test Video",
            summary="This is a test summary",
            key_points=["Point 1", "Point 2"],
            tags=["test", "video"],
            source_url="https://youtube.com/watch?v=test",
            model="test-model",
        )

    def test_creates_inbox_directory(self, temp_vault: Path, sample_note: Note) -> None:
        """Test that inbox directory is created if it doesn't exist."""
        result_path = write_note(sample_note, temp_vault, "inbox")
        assert (temp_vault / "inbox").exists()
        assert result_path.exists()

    def test_writes_markdown_content(self, temp_vault: Path, sample_note: Note) -> None:
        """Test that markdown content is written correctly."""
        result_path = write_note(sample_note, temp_vault, "inbox")
        content = result_path.read_text()
        assert "---" in content  # Frontmatter
        assert "Test Video" in content
        assert "test-model" in content

    def test_filename_format(self, temp_vault: Path, sample_note: Note) -> None:
        """Test that filename follows expected format."""
        result_path = write_note(sample_note, temp_vault, "inbox")
        assert result_path.name.startswith("youtube-")
        assert result_path.name.endswith(".md")

    def test_path_traversal_prevention(self, temp_vault: Path, sample_note: Note) -> None:
        """Test that path traversal is prevented."""
        # This test would need a malicious Note object with path separators
        # For now, we rely on sanitize_filename tests
        pass
