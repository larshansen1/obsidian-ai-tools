"""Tests for YouTube transcript fetching functionality."""

import pytest

from obsidian_ai_tools.obsidian import sanitize_filename
from obsidian_ai_tools.youtube import InvalidYouTubeURLError, extract_video_id


class TestExtractVideoId:
    """Tests for extract_video_id function."""

    def test_extract_from_standard_url(self) -> None:
        """Test extraction from standard youtube.com/watch?v= URL."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_from_short_url(self) -> None:
        """Test extraction from youtu.be shortened URL."""
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_from_mobile_url(self) -> None:
        """Test extraction from mobile m.youtube.com URL."""
        url = "https://m.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_without_www(self) -> None:
        """Test extraction from URL without www."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_invalid_url_raises_error(self) -> None:
        """Test that invalid URL raises InvalidYouTubeURLError."""
        with pytest.raises(InvalidYouTubeURLError):
            extract_video_id("https://example.com/video")

    def test_missing_video_id_raises_error(self) -> None:
        """Test that URL without video ID raises error."""
        with pytest.raises(InvalidYouTubeURLError):
            extract_video_id("https://www.youtube.com/watch")


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_basic_sanitization(self) -> None:
        """Test basic character sanitization."""
        result = sanitize_filename("Hello World")
        assert result == "hello-world"

    def test_special_characters_removed(self) -> None:
        """Test that special characters are removed."""
        result = sanitize_filename('Test: Video | "Special" <Chars>')
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result
        assert '"' not in result

    def test_multiple_spaces_collapsed(self) -> None:
        """Test that multiple spaces are collapsed to single hyphen."""
        result = sanitize_filename("Multiple    Spaces    Here")
        assert result == "multiple-spaces-here"

    def test_length_limit(self) -> None:
        """Test that long titles are truncated."""
        long_title = "A" * 150
        result = sanitize_filename(long_title, max_length=100)
        assert len(result) <= 100

    def test_empty_string_fallback(self) -> None:
        """Test that empty string returns fallback."""
        result = sanitize_filename("")
        assert result == "untitled-note"

    def test_only_special_chars_fallback(self) -> None:
        """Test that string with only special chars returns fallback."""
        result = sanitize_filename("***///:::<<<")
        assert result == "untitled-note"
