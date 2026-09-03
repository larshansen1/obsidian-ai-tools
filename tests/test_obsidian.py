"""Tests for Obsidian file writing functionality."""

from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_ai_tools.models import Note
from obsidian_ai_tools.obsidian import (
    FileWriteError,
    PathTraversalError,
    build_filename,
    build_obsidian_url,
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

    def test_default_max_length(self) -> None:
        """The default maximum length is 100 characters."""
        result = sanitize_filename("a" * 101)
        assert len(result) == 100

    def test_truncation_removes_trailing_hyphen(self) -> None:
        """Truncation must not leave a trailing hyphen."""
        result = sanitize_filename("a" * 49 + "-" + "b" * 50, max_length=50)
        assert result == "a" * 49

    def test_truncation_keeps_non_hyphen_boundary(self) -> None:
        """Only hyphens (not other letters) are stripped at the cut."""
        result = sanitize_filename("a" * 49 + "x" + "b" * 50, max_length=50)
        assert result == "a" * 49 + "x"

    def test_strips_leading_hyphens(self) -> None:
        """Leading hyphens are stripped from the sanitized name."""
        assert sanitize_filename("-foo") == "foo"


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

    def test_exact_filename(self) -> None:
        """The filename follows the documented format exactly."""
        assert build_filename("youtube", "My Video Title") == "youtube-my-video-title.md"


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

    def test_default_inbox_folder(self, temp_vault: Path, sample_note: Note) -> None:
        """Without an explicit folder the note lands in 'inbox'."""
        result_path = write_note(sample_note, temp_vault)
        assert result_path.parent == temp_vault / "inbox"

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

    def test_write_note_wraps_directory_creation_error(
        self, temp_vault: Path, sample_note: Note
    ) -> None:
        """Directory creation failures should become FileWriteError."""
        from obsidian_ai_tools.obsidian import FileWriteError

        with (
            patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")),
            pytest.raises(FileWriteError, match="Failed to create inbox directory"),
        ):
            write_note(sample_note, temp_vault)

    def test_write_note_wraps_path_resolution_error(
        self, temp_vault: Path, sample_note: Note
    ) -> None:
        """Path validation failures should become FileWriteError."""
        from obsidian_ai_tools.obsidian import FileWriteError

        with (
            patch("pathlib.Path.resolve", side_effect=OSError("broken path")),
            pytest.raises(FileWriteError, match="Path validation failed"),
        ):
            write_note(sample_note, temp_vault)

    def test_write_note_wraps_write_error(self, temp_vault: Path, sample_note: Note) -> None:
        """Write failures should become FileWriteError."""
        with (
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
            pytest.raises(FileWriteError, match="Failed to write note"),
        ):
            write_note(sample_note, temp_vault)

    def test_write_note_resolve_error_exact_message(
        self, temp_vault: Path, sample_note: Note
    ) -> None:
        """Resolve failures keep the underlying error message."""
        with (
            patch("pathlib.Path.resolve", side_effect=OSError("boom")),
            pytest.raises(FileWriteError) as exc_info,
        ):
            write_note(sample_note, temp_vault)

        assert str(exc_info.value) == "Path validation failed: boom"

    def test_target_path_write_error_exact_message(self, tmp_path: Path) -> None:
        """Target-path write failures keep the target in the message."""
        existing = tmp_path / "inbox" / "note.md"
        existing.parent.mkdir(parents=True)
        with (
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
            pytest.raises(FileWriteError) as exc_info,
        ):
            write_note(self._sample_note(), tmp_path, target_path=existing)

        message = str(exc_info.value)
        assert message.startswith("Failed to write note to ")
        assert message.endswith("disk full")

    def _sample_note(self) -> Note:
        """Build a minimal valid note."""
        return Note(
            title="Test Video",
            summary="Test summary",
            tags=["test"],
            source_url="https://example.com",
            model="test-model",
        )

    def test_filename_separator_rejected_forward(self, temp_vault: Path) -> None:
        """Forward slashes in the generated filename are rejected."""
        note = Note(
            title="X",
            summary="s",
            tags=[],
            source_url="u",
            model="m",
            source_type="weird/path",
        )

        with pytest.raises(PathTraversalError) as exc_info:
            write_note(note, temp_vault)

        assert str(exc_info.value) == "Filename contains path separators: weird/path-x.md"

    def test_filename_separator_rejected_backslash(self, temp_vault: Path) -> None:
        """Backslashes in the generated filename are rejected."""
        note = Note(
            title="X",
            summary="s",
            tags=[],
            source_url="u",
            model="m",
            source_type="weird\\path",
        )

        with pytest.raises(PathTraversalError):
            write_note(note, temp_vault)

    def test_target_path_outside_vault_exact_message(self, tmp_path: Path) -> None:
        """Target paths outside the vault report the attempted path."""
        vault = tmp_path / "vault"
        vault.mkdir()
        outside = tmp_path / "elsewhere.md"

        with pytest.raises(PathTraversalError) as exc_info:
            write_note(self._sample_note(), vault, target_path=outside)

        assert str(exc_info.value) == f"Update target outside vault: {outside}"


class TestBuildObsidianUrl:
    """Tests for build_obsidian_url function."""

    def test_basic_url(self) -> None:
        """Test URL for a simple vault and note path."""
        url = build_obsidian_url(Path("/vaults/notes"), Path("/vaults/notes/inbox/web-note.md"))
        assert url == "obsidian://open?vault=notes&file=inbox%2Fweb-note.md"

    def test_encodes_special_characters(self) -> None:
        """Test spaces and ampersands are percent-encoded."""
        url = build_obsidian_url(Path("/vaults/My Vault"), Path("/vaults/My Vault/inbox/q & a.md"))
        assert url == "obsidian://open?vault=My%20Vault&file=inbox%2Fq%20%26%20a.md"

    def test_raises_for_file_outside_vault(self) -> None:
        """Test files outside the vault are rejected."""
        with pytest.raises(ValueError):
            build_obsidian_url(Path("/vaults/notes"), Path("/elsewhere/note.md"))


class TestWriteNoteTargetPath:
    """Tests for update-in-place writes via target_path."""

    @pytest.fixture
    def sample_note(self) -> Note:
        return Note(
            title="Regenerated Title",
            summary="New summary",
            tags=["test"],
            source_url="https://example.com",
            model="test-model",
        )

    def test_overwrites_target_keeping_filename(self, tmp_path: Path, sample_note: Note) -> None:
        existing = tmp_path / "inbox" / "web-old-title.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("old content", encoding="utf-8")

        result = write_note(sample_note, tmp_path, target_path=existing)

        assert result == existing
        assert "Regenerated Title" in existing.read_text(encoding="utf-8")
        assert list((tmp_path / "inbox").glob("*.md")) == [existing]

    def test_rejects_target_outside_vault(self, tmp_path: Path, sample_note: Note) -> None:
        from obsidian_ai_tools.obsidian import PathTraversalError

        vault = tmp_path / "vault"
        vault.mkdir()
        outside = tmp_path / "elsewhere.md"

        with pytest.raises(PathTraversalError):
            write_note(sample_note, vault, target_path=outside)
