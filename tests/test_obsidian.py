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

    def test_path_traversal_prevention(self, temp_vault: Path) -> None:
        """Test that path traversal attempts are blocked."""
        from obsidian_ai_tools.obsidian import PathTraversalError

        # Test various path traversal attempts
        malicious_titles = [
            "../../../etc/passwd",  # Unix path traversal
            "..\\..\\..\\windows\\system32",  # Windows path traversal
            "/etc/passwd",  # Absolute Unix path
            "C:\\Windows\\System32",  # Absolute Windows path
            "test/../../../etc/passwd",  # Mixed with legitimate name
            "....//....//etc/passwd",  # Double dot slash
            "test/../../secret",  # Nested traversal
        ]

        for malicious_title in malicious_titles:
            note = Note(
                title=malicious_title,
                summary="Test summary",
                tags=["test"],
                source_url="https://example.com",
                model="test-model",
            )

            # Should either sanitize the title or raise PathTraversalError
            try:
                result_path = write_note(note, temp_vault, "inbox")

                # If write succeeds, verify file is INSIDE vault/inbox
                inbox_path = (temp_vault / "inbox").resolve()
                assert str(result_path.resolve()).startswith(str(inbox_path)), (
                    f"Path traversal: {malicious_title} created file outside inbox: "
                    f"{result_path.resolve()}"
                )

                # Verify no directory traversal occurred
                assert result_path.parent == inbox_path, (
                    f"File created in wrong directory: {result_path.parent} != {inbox_path}"
                )

            except PathTraversalError:
                # This is acceptable - explicit rejection
                pass

    def test_sanitize_filename_removes_path_separators(self) -> None:
        """Test that sanitize_filename removes all path separators."""
        # Unix separator
        result = sanitize_filename("test/path/here")
        assert "/" not in result
        assert result == "testpathhere"

        # Windows separator
        result = sanitize_filename("test\\path\\here")
        assert "\\" not in result
        assert result == "testpathhere"

    def test_sanitize_filename_removes_absolute_path_indicators(self) -> None:
        """Test that absolute paths are sanitized."""
        # Unix absolute path
        result = sanitize_filename("/etc/passwd")
        assert not result.startswith("/")
        assert "/" not in result

        # Windows absolute path
        result = sanitize_filename("C:\\Windows\\System32")
        assert ":" not in result
        assert "\\" not in result

    def test_write_note_rejects_symlink_attacks(self, temp_vault: Path) -> None:
        """Test that symlink-based path traversal is prevented."""
        from obsidian_ai_tools.obsidian import PathTraversalError

        # Create a symlink in inbox pointing outside vault
        inbox_path = temp_vault / "inbox"
        inbox_path.mkdir(parents=True, exist_ok=True)

        # Create target outside vault
        outside_path = temp_vault.parent / "outside_vault"
        outside_path.mkdir(exist_ok=True)

        # Create symlink (may not work on all systems)
        try:
            symlink_path = inbox_path / "symlink_escape"
            symlink_path.symlink_to(outside_path, target_is_directory=True)

            # Try to write a note with a title that would use the symlink
            note = Note(
                title="symlink_escape/malicious",
                summary="Test",
                tags=["test"],
                source_url="https://example.com",
                model="test-model",
            )

            # Should either reject or sanitize
            result_path = write_note(note, temp_vault, "inbox")

            # If it succeeds, verify it stayed in inbox
            inbox_resolved = inbox_path.resolve()
            result_resolved = result_path.resolve()

            assert str(result_resolved).startswith(str(inbox_resolved)), (
                "Symlink attack allowed file outside inbox"
            )

        except (PathTraversalError, OSError):
            # PathTraversalError = blocked correctly
            # OSError = symlink creation failed (acceptable on some systems)
            pass
