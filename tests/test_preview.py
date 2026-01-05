"""Tests for preview functionality."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from obsidian_ai_tools.preview import (
    PreviewInfo,
    ReadingListEntry,
    UnsupportedURLError,
    detect_source_type,
    estimate_cost,
    extract_topics,
    format_preview_json,
    format_preview_terminal,
    generate_preview,
    load_reading_list,
    save_to_reading_list,
    update_reading_list_status,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_preview() -> PreviewInfo:
    """Create a sample preview for testing."""
    return PreviewInfo(
        url="https://www.youtube.com/watch?v=test123",
        source_type="youtube",
        title="Test Video Title",
        content_length=5000,
        duration="23:15",
        estimated_cost_usd=0.0220,
        key_topics=["api", "programming", "tutorial"],
    )


@pytest.fixture
def sample_reading_list_entry(sample_preview: PreviewInfo) -> ReadingListEntry:
    """Create a sample reading list entry."""
    return ReadingListEntry(
        url=sample_preview.url,
        preview=sample_preview,
        status="pending",
    )


# =============================================================================
# Tests for PreviewInfo Model
# =============================================================================


class TestPreviewInfo:
    """Tests for PreviewInfo Pydantic model."""

    def test_preview_info_creation(self) -> None:
        """Test creating a PreviewInfo with required fields."""
        preview = PreviewInfo(
            url="https://example.com",
            source_type="web",
            title="Test Article",
            content_length=1000,
            estimated_cost_usd=0.0040,
        )

        assert preview.url == "https://example.com"
        assert preview.source_type == "web"
        assert preview.duration is None
        assert preview.key_topics == []

    def test_preview_info_serialization(self, sample_preview: PreviewInfo) -> None:
        """Test serialization to JSON."""
        json_str = sample_preview.model_dump_json()
        data = json.loads(json_str)

        assert data["url"] == sample_preview.url
        assert data["source_type"] == "youtube"
        assert data["content_length"] == 5000


class TestReadingListEntry:
    """Tests for ReadingListEntry model."""

    def test_reading_list_entry_creation(
        self, sample_preview: PreviewInfo
    ) -> None:
        """Test creating a ReadingListEntry."""
        entry = ReadingListEntry(url="https://example.com", preview=sample_preview)

        assert entry.status == "pending"
        assert entry.preview.title == sample_preview.title

    def test_reading_list_entry_serialization(
        self, sample_reading_list_entry: ReadingListEntry
    ) -> None:
        """Test serialization to JSON."""
        json_str = sample_reading_list_entry.model_dump_json()
        data = json.loads(json_str)

        assert data["status"] == "pending"
        assert "preview" in data


# =============================================================================
# Tests for Cost Estimation
# =============================================================================


class TestEstimateCost:
    """Tests for estimate_cost function."""

    def test_estimate_cost_basic(self) -> None:
        """Test basic cost estimation."""
        # 1000 words * 1.3 = 1300 input tokens
        # (1300 * 3 + 500 * 15) / 1_000_000 = 0.0114
        cost = estimate_cost(1000)
        assert cost == pytest.approx(0.0114, rel=0.01)

    def test_estimate_cost_larger_content(self) -> None:
        """Test cost estimation for larger content."""
        # 10000 words
        cost = estimate_cost(10000)
        assert cost > estimate_cost(1000)

    def test_estimate_cost_zero(self) -> None:
        """Test cost estimation for zero content."""
        cost = estimate_cost(0)
        # Should still include output token cost
        # (0 * 3 + 500 * 15) / 1_000_000 = 0.0075
        assert cost == pytest.approx(0.0075, rel=0.01)


# =============================================================================
# Tests for Topic Extraction
# =============================================================================


class TestExtractTopics:
    """Tests for extract_topics function."""

    def test_extract_topics_basic(self) -> None:
        """Test basic topic extraction."""
        text = "Python programming is great. Python code is readable."
        topics = extract_topics(text, top_n=3)

        assert "python" in topics
        assert len(topics) <= 3

    def test_extract_topics_filters_stopwords(self) -> None:
        """Test that stop words are filtered."""
        text = "The quick brown fox jumps over the lazy dog"
        topics = extract_topics(text)

        assert "the" not in topics
        assert "over" not in topics

    def test_extract_topics_empty_text(self) -> None:
        """Test handling of empty text."""
        topics = extract_topics("")
        assert topics == []

    def test_extract_topics_respects_top_n(self) -> None:
        """Test that top_n limit is respected."""
        text = "apple banana cherry date elderberry fig grape honeydew"
        topics = extract_topics(text, top_n=3)
        assert len(topics) <= 3


# =============================================================================
# Tests for Source Type Detection
# =============================================================================


class TestDetectSourceType:
    """Tests for detect_source_type function."""

    def test_detect_youtube_full_url(self) -> None:
        """Test detecting YouTube from full URL."""
        assert detect_source_type("https://www.youtube.com/watch?v=abc123") == "youtube"

    def test_detect_youtube_short_url(self) -> None:
        """Test detecting YouTube from short URL."""
        assert detect_source_type("https://youtu.be/abc123") == "youtube"

    def test_detect_pdf_extension(self) -> None:
        """Test detecting PDF from extension."""
        assert detect_source_type("https://example.com/paper.pdf") == "pdf"

    def test_detect_pdf_path(self) -> None:
        """Test detecting PDF from URL path."""
        assert detect_source_type("https://example.com/pdf/12345") == "pdf"

    def test_detect_web_article(self) -> None:
        """Test detecting web article."""
        assert detect_source_type("https://example.com/blog/post") == "web"

    def test_detect_unsupported_raises(self) -> None:
        """Test that unsupported URLs raise error."""
        with pytest.raises(UnsupportedURLError):
            detect_source_type("ftp://example.com/file")


# =============================================================================
# Tests for Reading List Persistence
# =============================================================================


class TestReadingListPersistence:
    """Tests for reading list save/load functions."""

    def test_save_and_load_reading_list(
        self, tmp_path: Path, sample_reading_list_entry: ReadingListEntry
    ) -> None:
        """Test saving and loading reading list."""
        save_to_reading_list(sample_reading_list_entry, tmp_path)

        entries = load_reading_list(tmp_path)
        assert len(entries) == 1
        assert entries[0].url == sample_reading_list_entry.url

    def test_load_empty_reading_list(self, tmp_path: Path) -> None:
        """Test loading non-existent reading list."""
        entries = load_reading_list(tmp_path)
        assert entries == []

    def test_append_to_reading_list(
        self, tmp_path: Path, sample_preview: PreviewInfo
    ) -> None:
        """Test appending multiple entries."""
        entry1 = ReadingListEntry(url="https://example.com/1", preview=sample_preview)
        entry2 = ReadingListEntry(url="https://example.com/2", preview=sample_preview)

        save_to_reading_list(entry1, tmp_path)
        save_to_reading_list(entry2, tmp_path)

        entries = load_reading_list(tmp_path)
        assert len(entries) == 2

    def test_update_reading_list_status(
        self, tmp_path: Path, sample_reading_list_entry: ReadingListEntry
    ) -> None:
        """Test updating entry status."""
        save_to_reading_list(sample_reading_list_entry, tmp_path)

        updated = update_reading_list_status(
            sample_reading_list_entry.url, "ingested", tmp_path
        )
        assert updated is True

        entries = load_reading_list(tmp_path)
        assert entries[0].status == "ingested"

    def test_update_nonexistent_entry(self, tmp_path: Path) -> None:
        """Test updating non-existent entry returns False."""
        updated = update_reading_list_status(
            "https://nonexistent.com", "ingested", tmp_path
        )
        assert updated is False


# =============================================================================
# Tests for Preview Generation
# =============================================================================


class TestGeneratePreview:
    """Tests for generate_preview function."""

    @patch("requests.get")
    def test_generate_preview_web(self, mock_get: MagicMock) -> None:
        """Test generating preview for web URL."""
        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html>
        <head><title>Test Article Title</title></head>
        <body>
        <h1>Test Article</h1>
        <p>This is the article content with many words about programming
        and software development and Python and JavaScript.</p>
        </body>
        </html>
        """
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        preview = generate_preview("https://example.com/article")

        assert preview.source_type == "web"
        assert preview.title is not None
        assert preview.estimated_cost_usd > 0

    @patch("requests.head")
    def test_generate_preview_pdf(self, mock_head: MagicMock) -> None:
        """Test generating preview for PDF URL."""
        mock_response = MagicMock()
        mock_response.headers = {"Content-Length": "500000"}
        mock_head.return_value = mock_response

        preview = generate_preview("https://example.com/paper.pdf")

        assert preview.source_type == "pdf"
        assert preview.content_length > 0

    def test_generate_preview_unsupported_url(self) -> None:
        """Test that unsupported URL raises error."""
        with pytest.raises(UnsupportedURLError):
            generate_preview("ftp://example.com/file")


# =============================================================================
# Tests for Formatters
# =============================================================================


class TestFormatPreviewTerminal:
    """Tests for format_preview_terminal function."""

    def test_format_terminal_includes_title(self, sample_preview: PreviewInfo) -> None:
        """Test terminal format includes title."""
        output = format_preview_terminal(sample_preview)
        assert sample_preview.title in output

    def test_format_terminal_includes_cost(self, sample_preview: PreviewInfo) -> None:
        """Test terminal format includes cost."""
        output = format_preview_terminal(sample_preview)
        assert "$" in output

    def test_format_terminal_includes_duration(self, sample_preview: PreviewInfo) -> None:
        """Test terminal format includes duration for videos."""
        output = format_preview_terminal(sample_preview)
        assert "23:15" in output

    def test_format_terminal_includes_topics(self, sample_preview: PreviewInfo) -> None:
        """Test terminal format includes topics."""
        output = format_preview_terminal(sample_preview)
        assert "api" in output


class TestFormatPreviewJson:
    """Tests for format_preview_json function."""

    def test_format_json_valid(self, sample_preview: PreviewInfo) -> None:
        """Test JSON format is valid."""
        output = format_preview_json(sample_preview)
        data = json.loads(output)

        assert data["url"] == sample_preview.url
        assert data["source_type"] == "youtube"


# =============================================================================
# CLI Integration Tests
# =============================================================================


class TestPreviewCommand:
    """Integration tests for kai preview CLI command."""

    def test_preview_command_no_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test preview fails without URL."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(app, ["preview"])

        assert result.exit_code == 1
        assert "No URL provided" in result.output

    @patch("obsidian_ai_tools.preview.generate_preview")
    def test_preview_command_success(
        self,
        mock_generate: MagicMock,
        tmp_path: Path,
        sample_preview: PreviewInfo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test successful preview."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.return_value = sample_preview

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(
            app, ["preview", "https://example.com", "--vault", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "Preview" in result.output

    @patch("obsidian_ai_tools.preview.generate_preview")
    def test_preview_command_json_format(
        self,
        mock_generate: MagicMock,
        tmp_path: Path,
        sample_preview: PreviewInfo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test preview with JSON output."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.return_value = sample_preview

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(
            app, ["preview", "https://example.com", "--format", "json", "--vault", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "{" in result.output

    @patch("obsidian_ai_tools.preview.generate_preview")
    def test_preview_command_unsupported_url(
        self,
        mock_generate: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test preview with unsupported URL."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.side_effect = UnsupportedURLError("Cannot determine source type")

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(
            app, ["preview", "ftp://invalid.com", "--vault", str(tmp_path)]
        )

        # Should handle gracefully (not crash)
        assert "Unsupported" in result.output or "Cannot determine" in result.output
