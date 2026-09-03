"""Tests for preview functionality."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from obsidian_ai_tools.preview import (
    PreviewError,
    PreviewInfo,
    ReadingListEntry,
    UnsupportedURLError,
    detect_source_type,
    estimate_cost,
    extract_topics,
    format_preview_json,
    format_preview_terminal,
    generate_preview,
    save_to_reading_list,
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

    def test_reading_list_entry_creation(self, sample_preview: PreviewInfo) -> None:
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

    def test_estimate_cost_large_precision(self) -> None:
        """Huge content lengths must still divide by exactly 1_000_000."""
        # (int(1e9 * 1.3) * 3 + 500 * 15) / 1_000_000 = 3900.0075
        assert estimate_cost(10**9) == 3900.0075

    def test_estimate_cost_rounding_boundary(self) -> None:
        """Costs straddling a 5th-decimal boundary pin the 4-decimal round."""
        # content_length 12 -> input_tokens 15 -> cost 0.007545
        # round(..., 4) = 0.0075; round(..., 5) = 0.00754; output 501 -> 0.0076
        assert estimate_cost(12) == 0.0075


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

    def test_extract_topics_default_top_n(self) -> None:
        """The default top_n must return exactly 5 of 6 distinct topics."""
        topics = extract_topics("apple banana cherry date elderberry garlic")
        assert len(topics) == 5

    def test_extract_topics_excludes_three_letter_words(self) -> None:
        """Three-letter words are filtered by the len > 3 rule."""
        assert extract_topics("cat cat cat") == []

    def test_extract_topics_includes_four_letter_words(self) -> None:
        """Four-letter words are kept, locking the threshold at > 3."""
        assert extract_topics("code code code") == ["code"]


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

    def test_detect_web_http_scheme(self) -> None:
        """Plain http:// URLs must classify as web too."""
        assert detect_source_type("http://example.com/article") == "web"

    def test_detect_unsupported_raises(self) -> None:
        """Test that unsupported URLs raise error."""
        with pytest.raises(UnsupportedURLError):
            detect_source_type("ftp://example.com/file")

    def test_detect_unsupported_raises_full_message(self) -> None:
        """The unsupported-URL error message must include the original URL."""
        with pytest.raises(
            UnsupportedURLError, match="Cannot determine source type for: ftp://example.com/file"
        ):
            detect_source_type("ftp://example.com/file")


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

    @patch("obsidian_ai_tools.youtube.YouTubeClient")
    def test_generate_preview_youtube_uses_duration(self, client_cls: MagicMock) -> None:
        """YouTube previews should estimate words from H:MM:SS duration."""
        client_cls.return_value._fetch_metadata.return_value = {
            "title": "Long Video",
            "duration": "1:30:00",
        }

        preview = generate_preview("https://youtube.com/watch?v=abc")

        assert preview.title == "Long Video"
        assert preview.content_length == 90 * 150
        assert preview.duration == "1:30:00"
        assert preview.source_type == "youtube"
        assert preview.key_topics == ["long", "video"]

    @patch("obsidian_ai_tools.youtube.YouTubeClient")
    def test_generate_preview_youtube_fallback_title_and_video_id(
        self, client_cls: MagicMock
    ) -> None:
        """Missing titles use the exact fallback; metadata is fetched by video id."""
        client_cls.return_value._fetch_metadata.return_value = {"duration": "5:00"}

        preview = generate_preview("https://youtube.com/watch?v=abc")

        client_cls.return_value._fetch_metadata.assert_called_once_with("abc")
        assert preview.title == "Unknown Video"
        assert preview.content_length == 750

    @pytest.mark.parametrize(
        "duration,expected",
        [
            ("25:00", 3750),  # MM:SS -> minutes * 150
            ("61", 150),  # seconds just under an hour -> 1 minute
            ("30", 150),  # seconds under threshold -> 1 minute
            ("1250", 3000),  # seconds over threshold -> floor division by 60
            ("2400", 6000),  # large seconds pin the // 60 divisor
            ("0:0:0:0", 0),  # unexpected part count leaves the default estimate
        ],
    )
    @patch("obsidian_ai_tools.youtube.YouTubeClient")
    def test_generate_preview_youtube_duration_parsing(
        self, client_cls: MagicMock, duration: str, expected: int
    ) -> None:
        """Each duration shape pins the exact minute conversion."""
        client_cls.return_value._fetch_metadata.return_value = {
            "title": "Titled Video",
            "duration": duration,
        }

        preview = generate_preview("https://youtube.com/watch?v=abc")

        assert preview.content_length == expected
        assert preview.duration == duration

    @patch("obsidian_ai_tools.preview.logger.error")
    @patch("obsidian_ai_tools.youtube.YouTubeClient")
    def test_generate_preview_youtube_wraps_unexpected_failure(
        self, client_cls: MagicMock, mock_error: MagicMock
    ) -> None:
        """Non-preview exceptions bubble as PreviewError with a descriptive log."""
        client_cls.return_value._fetch_metadata.side_effect = RuntimeError("boom")

        with pytest.raises(PreviewError, match="Failed to generate preview"):
            generate_preview("https://youtube.com/watch?v=abc")

        assert mock_error.call_args[0][0].startswith("Preview failed for")
        assert "boom" in mock_error.call_args[0][0]

    @patch("obsidian_ai_tools.youtube.YouTubeClient")
    def test_generate_preview_youtube_uses_default_without_duration(
        self, client_cls: MagicMock
    ) -> None:
        """YouTube previews should retain a default estimate without duration."""
        client_cls.return_value._fetch_metadata.return_value = {"title": "Short Video"}

        preview = generate_preview("https://youtube.com/watch?v=abc")

        assert preview.content_length == 5000

    @patch("requests.get")
    def test_generate_preview_web_falls_back_to_h1(self, mock_get: MagicMock) -> None:
        """Web previews should use the first heading when title metadata is absent."""
        mock_get.return_value.text = (
            "<html><body><h1>Fallback Heading</h1><p>body words</p></body></html>"
        )

        preview = generate_preview("https://example.com/no-title")

        assert preview.title == "Fallback Heading"

    @patch("requests.get", side_effect=OSError("offline"))
    def test_generate_preview_web_wraps_fetch_error(self, mock_get: MagicMock) -> None:
        """Web preview fetch errors should use the public PreviewError."""
        from obsidian_ai_tools.preview import PreviewError

        with pytest.raises(PreviewError, match="Failed to preview web page"):
            generate_preview("https://example.com/offline")

    @patch("requests.get")
    def test_generate_preview_web_call_args(self, mock_get: MagicMock) -> None:
        """Exact requests.get call pins url, timeout and User-Agent header."""
        mock_get.return_value.text = "<html><body><p>hello world</p></body></html>"

        generate_preview("https://example.com/fixed")

        mock_get.assert_called_once_with(
            "https://example.com/fixed",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ObsidianAI/1.0)"},
        )

    @patch("requests.get")
    def test_generate_preview_web_unknown_article_fallback(self, mock_get: MagicMock) -> None:
        """HTML without title or h1 must keep the exact 'Unknown Article' fallback."""
        mock_get.return_value.text = "<html><body><p>hello world</p></body></html>"

        preview = generate_preview("https://example.com/plain")

        assert preview.title == "Unknown Article"

    @patch("requests.get")
    def test_generate_preview_web_uses_title_tag(self, mock_get: MagicMock) -> None:
        """A real <title> must replace the fallback string."""
        mock_get.return_value.text = (
            "<html><head><title>Real Title</title></head><body><p>body text words</p></body></html>"
        )

        preview = generate_preview("https://example.com/titled")

        assert preview.title == "Real Title"

    @patch("requests.get")
    def test_generate_preview_web_h1_strip(self, mock_get: MagicMock) -> None:
        """h1 headings must be whitespace-stripped for the title."""
        mock_get.return_value.text = (
            "<html><body><h1>  Spaced Heading  </h1><p>words</p></body></html>"
        )

        preview = generate_preview("https://example.com/h1")

        assert preview.title == "Spaced Heading"

    @patch("requests.get")
    def test_generate_preview_web_word_count_uses_separator(self, mock_get: MagicMock) -> None:
        """Paragraph text must be joined with a space for word counting."""
        mock_get.return_value.text = "<html><body><p>hello</p><p>world</p></body></html>"

        preview = generate_preview("https://example.com/sep")

        assert preview.content_length == 2

    @patch("requests.get")
    def test_generate_preview_web_topics_from_text(self, mock_get: MagicMock) -> None:
        """key_topics must come from the extracted body text."""
        mock_get.return_value.text = (
            "<html><body><p>python programming development python</p></body></html>"
        )

        preview = generate_preview("https://example.com/topics")

        assert preview.key_topics == ["python", "programming", "development"]

    @patch("requests.get")
    def test_generate_preview_web_long_title_truncated(self, mock_get: MagicMock) -> None:
        """Titles longer than 200 characters must be truncated to 200."""
        mock_get.return_value.text = (
            "<html><head><title>" + "a" * 205 + "</title></head><body><p>w</p></body></html>"
        )

        preview = generate_preview("https://example.com/long")

        assert len(preview.title) == 200

    @patch("requests.get")
    def test_generate_preview_web_slice_boundary(self, mock_get: MagicMock) -> None:
        """A topic straddling the 2000-char preview slice pins the exact cut."""
        # 998 * "b " + "abcde" -> 2001 chars; the slice [:2000] leaves 'abcd'
        mock_get.return_value.text = "<html><body><p>" + "b " * 998 + "abcde</p></body></html>"

        preview = generate_preview("https://example.com/slice")

        assert preview.key_topics == ["abcd"]

    @patch("bs4.BeautifulSoup")
    @patch("requests.get")
    def test_generate_preview_web_bs4_parser_arg(
        self, mock_get: MagicMock, mock_bs4: MagicMock
    ) -> None:
        """BeautifulSoup is built from response text with the html.parser feature."""
        mock_get.return_value.text = "<html><body><p>hello</p></body></html>"
        soup = MagicMock()
        soup.title = MagicMock()
        soup.title.string = "Parsed Title"
        soup.get_text.return_value = "hello world words here"
        soup.find.return_value = None
        mock_bs4.return_value = soup

        generate_preview("https://example.com/bs4")

        assert mock_bs4.call_args.args == (mock_get.return_value.text, "html.parser")

    @patch("obsidian_ai_tools.preview.logger.warning")
    @patch("requests.get", side_effect=OSError("offline"))
    def test_generate_preview_web_logs_warning(
        self, mock_get: MagicMock, mock_warning: MagicMock
    ) -> None:
        """Web failures log a descriptive warning before raising PreviewError."""
        with pytest.raises(PreviewError, match="Failed to preview web page"):
            generate_preview("https://example.com/offline")

        assert mock_warning.call_args[0][0].startswith("Web preview failed")
        assert "offline" in mock_warning.call_args[0][0]

    def test_generate_preview_local_pdf(self, tmp_path: Path) -> None:
        """Local PDF previews should estimate content from file size."""
        pdf = tmp_path / "local-report.pdf"
        pdf.write_bytes(b"x" * 250_000)

        preview = generate_preview(str(pdf))

        assert preview.title == "local report"
        assert preview.content_length == 1000

    def test_generate_preview_missing_local_pdf(self, tmp_path: Path) -> None:
        """Missing local PDFs should raise a public preview error."""
        from obsidian_ai_tools.preview import PreviewError

        with pytest.raises(PreviewError, match="PDF not found"):
            generate_preview(str(tmp_path / "missing.pdf"))

    @patch("requests.head")
    def test_generate_preview_remote_pdf_call_args(self, mock_head: MagicMock) -> None:
        """Exact requests.head call pins url, timeout and allow_redirects."""
        mock_head.return_value.headers = {"Content-Length": "300000"}

        generate_preview("https://example.com/docs/my-report_2024.pdf")

        mock_head.assert_called_once_with(
            "https://example.com/docs/my-report_2024.pdf",
            timeout=10,
            allow_redirects=True,
        )

    @patch("requests.head")
    def test_generate_preview_remote_pdf_size_and_title(self, mock_head: MagicMock) -> None:
        """Remote PDF page math and URL-derived titles are pinned exactly."""
        mock_head.return_value.headers = {"Content-Length": "300000"}

        preview = generate_preview("https://example.com/docs/my-report_2024.pdf")

        assert preview.title == "my report 2024"
        assert preview.content_length == 1500

    @patch("requests.head")
    def test_generate_preview_remote_pdf_float_pages(self, mock_head: MagicMock) -> None:
        """Non-multiples of 100KB must floor-divide page counts."""
        mock_head.return_value.headers = {"Content-Length": "250000"}

        preview = generate_preview("https://example.com/paper.pdf")

        assert preview.content_length == 1000

    @patch("requests.head")
    def test_generate_preview_remote_pdf_min_one_page(self, mock_head: MagicMock) -> None:
        """Small remote PDFs round up to at least one page."""
        mock_head.return_value.headers = {"Content-Length": "50000"}

        preview = generate_preview("https://example.com/paper.pdf")

        assert preview.content_length == 500

    @patch("requests.head")
    def test_generate_preview_remote_pdf_missing_content_length(self, mock_head: MagicMock) -> None:
        """Missing Content-Length defaults to zero bytes / one page."""
        mock_head.return_value.headers = {}

        preview = generate_preview("https://example.com/paper.pdf")

        assert preview.content_length == 500

    @patch("requests.head")
    def test_generate_preview_remote_pdf_http_scheme(self, mock_head: MagicMock) -> None:
        """Plain http:// PDF URLs must still take the remote branch."""
        mock_head.return_value.headers = {"Content-Length": "100000"}

        preview = generate_preview("http://example.com/paper.pdf")

        assert preview.content_length == 500

    @patch("requests.head")
    def test_generate_preview_remote_pdf_long_title_truncated(self, mock_head: MagicMock) -> None:
        """PDF titles derived from long URLs truncate to 200 characters."""
        mock_head.return_value.headers = {"Content-Length": "50000"}

        preview = generate_preview("https://example.com/" + "a" * 205 + ".pdf")

        assert len(preview.title) == 200

    def test_generate_preview_local_pdf_underscore_title(self, tmp_path: Path) -> None:
        """Local PDF stems translate dashes and underscores into spaces."""
        pdf = tmp_path / "my-report_2024.pdf"
        pdf.write_bytes(b"x" * 40_000)

        preview = generate_preview(str(pdf))

        assert preview.title == "my report 2024"
        assert preview.content_length == 500

    def test_generate_preview_local_pdf_large_file(self, tmp_path: Path) -> None:
        """Local PDF page math pins the 100KB-per-page divisor."""
        pdf = tmp_path / "big-report.pdf"
        pdf.write_bytes(b"x" * 300_000)

        preview = generate_preview(str(pdf))

        assert preview.content_length == 1500

    @patch("obsidian_ai_tools.preview.logger.warning")
    @patch("requests.head", side_effect=OSError("network down"))
    def test_generate_preview_remote_pdf_logs_warning(
        self, mock_head: MagicMock, mock_warning: MagicMock
    ) -> None:
        """PDF failures log a descriptive warning before raising PreviewError."""
        with pytest.raises(PreviewError, match="Failed to preview PDF"):
            generate_preview("https://example.com/paper.pdf")

        assert mock_warning.call_args[0][0].startswith("PDF preview failed")
        assert "network down" in mock_warning.call_args[0][0]


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

    def test_format_terminal_topics_join(self, sample_preview: PreviewInfo) -> None:
        """Topics must be joined with exactly ', '."""
        output = format_preview_terminal(sample_preview)
        assert "Key topics: api, programming, tutorial" in output

    def test_format_terminal_multiline(self, sample_preview: PreviewInfo) -> None:
        """Output sections must be newline-separated."""
        output = format_preview_terminal(sample_preview)
        assert "\n  Source: Youtube (23:15)" in output

    def test_format_terminal_source_with_duration(self, sample_preview: PreviewInfo) -> None:
        """The source line appends the duration to the capitalized type."""
        output = format_preview_terminal(sample_preview)
        assert "Source: Youtube (23:15)" in output


class TestFormatPreviewJson:
    """Tests for format_preview_json function."""

    def test_format_json_valid(self, sample_preview: PreviewInfo) -> None:
        """Test JSON format is valid."""
        output = format_preview_json(sample_preview)
        data = json.loads(output)

        assert data["url"] == sample_preview.url
        assert data["source_type"] == "youtube"

    def test_format_json_pretty_printed(self, sample_preview: PreviewInfo) -> None:
        """JSON output must be exactly the two-space-indented dump."""
        output = format_preview_json(sample_preview)
        assert "\n" in output
        assert output == sample_preview.model_dump_json(indent=2)


# =============================================================================
# Tests for Reading List Persistence
# =============================================================================


class TestSaveToReadingList:
    """Tests for save_to_reading_list._get_reading_list_path persistence."""

    def test_save_to_reading_list_appends(
        self, tmp_path: Path, sample_reading_list_entry: ReadingListEntry
    ) -> None:
        """Entries are appended as JSONL under vault/.kai/reading_list.jsonl."""
        save_to_reading_list(sample_reading_list_entry, tmp_path)

        path = tmp_path / ".kai" / "reading_list.jsonl"
        assert path.exists()
        assert path.read_text() == sample_reading_list_entry.model_dump_json() + "\n"

    def test_save_to_reading_list_second_append(
        self, tmp_path: Path, sample_reading_list_entry: ReadingListEntry
    ) -> None:
        """A second save appends without clobbering the previous entry."""
        save_to_reading_list(sample_reading_list_entry, tmp_path)
        save_to_reading_list(sample_reading_list_entry, tmp_path)

        lines = (tmp_path / ".kai" / "reading_list.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

    @patch("obsidian_ai_tools.preview.logger.info")
    def test_save_to_reading_list_logs_success(
        self,
        mock_info: MagicMock,
        tmp_path: Path,
        sample_reading_list_entry: ReadingListEntry,
    ) -> None:
        """Successful saves log the saved URL."""
        save_to_reading_list(sample_reading_list_entry, tmp_path)

        assert (
            mock_info.call_args[0][0] == f"Saved to reading list: {sample_reading_list_entry.url}"
        )

    @patch("obsidian_ai_tools.preview.logger.error")
    @patch("builtins.open", side_effect=OSError("disk full"))
    def test_save_to_reading_list_failure(
        self,
        mock_open: MagicMock,
        mock_error: MagicMock,
        tmp_path: Path,
        sample_reading_list_entry: ReadingListEntry,
    ) -> None:
        """I/O failures wrap as PreviewError with a descriptive log line."""
        with pytest.raises(PreviewError, match="Failed to save to reading list"):
            save_to_reading_list(sample_reading_list_entry, tmp_path)

        assert mock_error.call_args[0][0].startswith("Failed to save to reading list")


# =============================================================================
# CLI Integration Tests
# =============================================================================


class TestPreviewCommand:
    """Integration tests for kai preview CLI command.

    These tests need no environment setup of their own: the autouse
    ``_isolate_settings`` fixture in conftest already exports a valid
    ``OBSIDIAN_VAULT_PATH`` / ``OPENROUTER_API_KEY`` / ``LLM_MODEL`` trio, and
    every test that touches the vault passes ``--vault`` explicitly.
    """

    def test_preview_command_no_url(self) -> None:
        """Test preview fails without URL."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

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
    ) -> None:
        """Test successful preview."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.return_value = sample_preview

        runner = CliRunner()
        result = runner.invoke(app, ["preview", "https://example.com", "--vault", str(tmp_path)])

        assert result.exit_code == 0
        assert "Preview" in result.output

    @patch("obsidian_ai_tools.preview.generate_preview")
    def test_preview_command_json_format(
        self,
        mock_generate: MagicMock,
        tmp_path: Path,
        sample_preview: PreviewInfo,
    ) -> None:
        """Test preview with JSON output."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.return_value = sample_preview

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
    ) -> None:
        """Test preview with unsupported URL."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.side_effect = UnsupportedURLError("Cannot determine source type")

        runner = CliRunner()
        result = runner.invoke(app, ["preview", "ftp://invalid.com", "--vault", str(tmp_path)])

        # Should handle gracefully (not crash)
        assert "Unsupported" in result.output or "Cannot determine" in result.output

    @patch("obsidian_ai_tools.preview.generate_preview")
    @patch("obsidian_ai_tools.observability.get_db")
    def test_preview_command_batch_reports_summary(
        self,
        mock_get_db: MagicMock,
        mock_generate: MagicMock,
        tmp_path: Path,
        sample_preview: PreviewInfo,
    ) -> None:
        """Batch mode should process valid stdin URLs and report total cost."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.return_value = sample_preview

        result = CliRunner().invoke(
            app,
            ["preview", "--batch", "--vault", str(tmp_path)],
            input="https://example.com/one\ninvalid\nhttps://example.com/two\n",
        )

        assert result.exit_code == 0
        assert "Processing 2 URL(s)" in result.output
        assert "Previewed 2/2 URL(s)" in result.output
        assert "Total estimated cost" in result.output
        assert mock_get_db.return_value.record_metric.call_count == 2

    @patch("obsidian_ai_tools.preview.generate_preview")
    @patch("obsidian_ai_tools.preview.save_to_reading_list")
    @patch("obsidian_ai_tools.observability.get_db")
    def test_preview_command_interactive_save(
        self,
        mock_get_db: MagicMock,
        mock_save: MagicMock,
        mock_generate: MagicMock,
        tmp_path: Path,
        sample_preview: PreviewInfo,
    ) -> None:
        """Interactive preview should save the selected URL to the reading list."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        mock_generate.return_value = sample_preview

        result = CliRunner().invoke(
            app,
            ["preview", sample_preview.url, "--interactive", "--vault", str(tmp_path)],
            input="s\n",
        )

        assert result.exit_code == 0
        assert "Saved to reading list" in result.output
        mock_save.assert_called_once()
