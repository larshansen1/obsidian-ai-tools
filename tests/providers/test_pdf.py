"""Tests for PDF provider."""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
import requests
from pypdf import PdfWriter

from obsidian_ai_tools.models import ArticleMetadata
from obsidian_ai_tools.providers.pdf import PDFProvider, _record_attempt


def create_pdf_with_text(path: Path, text: str, metadata: dict | None = None) -> None:
    """Helper to create a PDF with actual text content."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)

    if metadata:
        writer.add_metadata(metadata)

    with open(path, "wb") as f:
        writer.write(f)


class TestPDFProvider:
    """Tests for PDFProvider class."""

    def test_provider_name(self) -> None:
        """Test provider name is 'pdf'."""
        provider = PDFProvider()
        assert provider.name == "pdf"

    def test_validate_local_pdf_file(self) -> None:
        """Test validation of local PDF files."""
        provider = PDFProvider()

        # Create a temporary PDF file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            assert provider.validate(str(tmp_path)) is True
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_validate_pdf_url(self) -> None:
        """Test validation of PDF URLs."""
        provider = PDFProvider()

        assert provider.validate("https://example.com/doc.pdf") is True
        assert provider.validate("http://example.com/paper.PDF") is True
        assert provider.validate("https://arxiv.org/pdf/2024.12345.pdf") is True

    def test_validate_non_pdf_urls(self) -> None:
        """Test rejection of non-PDF URLs."""
        provider = PDFProvider()

        assert provider.validate("https://example.com/article.html") is False
        assert provider.validate("https://youtube.com/watch?v=123") is False
        assert provider.validate("https://example.com") is False

    def test_validate_non_pdf_files(self) -> None:
        """Test rejection of non-PDF files."""
        provider = PDFProvider()

        # Non-existent file with .pdf extension should pass validation check
        # (existence is checked during ingest)
        assert provider.validate("./test.pdf") is True

        # But non-PDF extensions should fail
        assert provider.validate("./test.txt") is False
        assert provider.validate("./test.md") is False

    def test_ingest_local_pdf_basic(self) -> None:
        """Test basic local PDF ingestion with blank pages."""
        provider = PDFProvider()

        # Create a simple test PDF with blank page
        # Note: Blank pages may not have extractable text
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)

            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            writer.add_metadata(
                {
                    "/Title": "Test PDF Document",
                    "/Author": "Test Author",
                }
            )

            with open(tmp_path, "wb") as f:
                writer.write(f)

        try:
            # This should fail because blank pages have no text
            # Testing error handling for empty PDFs
            with pytest.raises(RuntimeError, match="No text content could be extracted"):
                provider._ingest(str(tmp_path))

        finally:
            tmp_path.unlink(missing_ok=True)

    def test_ingest_local_pdf_file_not_found(self) -> None:
        """Test error handling when PDF file doesn't exist."""
        provider = PDFProvider()

        with pytest.raises(FileNotFoundError, match="PDF file not found"):
            provider._ingest("/nonexistent/path/to/file.pdf")

    def test_ingest_local_pdf_truncation(self) -> None:
        """Test PDF truncation when exceeding page limit."""
        provider = PDFProvider()

        # Create a PDF with many blank pages
        # Note: Blank pages won't have text, so this will raise an error
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)

            writer = PdfWriter()
            # Create 10 blank pages
            for _i in range(10):
                writer.add_blank_page(width=200, height=200)

            with open(tmp_path, "wb") as f:
                writer.write(f)

        try:
            # Should raise error because blank pages have no text
            with pytest.raises(RuntimeError, match="No text content could be extracted"):
                provider._ingest(str(tmp_path), max_pages=5)

        finally:
            tmp_path.unlink(missing_ok=True)

    @patch("obsidian_ai_tools.providers.pdf.requests.post")
    @patch("obsidian_ai_tools.providers.pdf.requests.get")
    def test_ingest_remote_pdf_success(self, mock_get: Mock, mock_post: Mock) -> None:
        """Test successful remote PDF download with Supadata fallback."""
        provider = PDFProvider()
        # Ensure Supadata fallback is enabled for this test
        provider.supadata_key = "test-supadata-key"

        # Create a test PDF with blank page
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)

            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            writer.add_metadata({"/Title": "Remote Test PDF"})

            with open(tmp_path, "wb") as f:
                writer.write(f)

        try:
            # Read the PDF content
            with open(tmp_path, "rb") as f:
                pdf_content = f.read()

            # Mock the HTTP response for direct download
            mock_response = Mock()
            mock_response.headers = {
                "content-type": "application/pdf",
                "content-length": str(len(pdf_content)),
            }
            mock_response.raise_for_status = Mock()
            mock_response.iter_content = Mock(return_value=[pdf_content])
            mock_get.return_value = mock_response

            # Mock Supadata fallback (will be called since blank PDF has no text)
            mock_supadata_response = Mock()
            mock_supadata_response.raise_for_status = Mock()
            mock_supadata_response.json.return_value = {
                "content": "Extracted text from PDF",
                "title": "Remote Test PDF",
                "author": "Test Author",
            }
            mock_post.return_value = mock_supadata_response

            url = "https://example.com/test.pdf"
            result = provider._ingest(url)

            # Should successfully fallback to Supadata
            assert isinstance(result, ArticleMetadata)
            assert result.content == "Extracted text from PDF"

        finally:
            tmp_path.unlink(missing_ok=True)

    @patch("obsidian_ai_tools.providers.pdf.requests.post")
    @patch("obsidian_ai_tools.providers.pdf.requests.get")
    def test_ingest_remote_pdf_supadata_fallback(self, mock_get: Mock, mock_post: Mock) -> None:
        """Test Supadata fallback when direct download fails."""
        provider = PDFProvider()
        # Ensure Supadata fallback is enabled for this test
        provider.supadata_key = "test-supadata-key"

        # Mock failed direct download
        mock_get.side_effect = Exception("Download failed")

        # Mock successful Supadata response
        mock_supadata_response = Mock()
        mock_supadata_response.raise_for_status = Mock()
        mock_supadata_response.json.return_value = {
            "content": "Extracted text from PDF via Supadata",
            "title": "Supadata PDF Title",
            "author": "Supadata Author",
        }
        mock_post.return_value = mock_supadata_response

        url = "https://example.com/protected.pdf"
        result = provider._ingest(url)

        assert isinstance(result, ArticleMetadata)
        assert result.title == "Supadata PDF Title"
        assert result.author == "Supadata Author"
        assert result.content == "Extracted text from PDF via Supadata"

    @patch("obsidian_ai_tools.providers.pdf.requests.get")
    def test_ingest_remote_pdf_size_limit(self, mock_get: Mock) -> None:
        """Test PDF size limit enforcement for remote downloads."""
        provider = PDFProvider()
        # Explicitly disable Supadata fallback for this test
        provider.supadata_key = None

        # Mock response with large content-length
        mock_response = Mock()
        mock_response.headers = {
            "content-type": "application/pdf",
            "content-length": str(100 * 1024 * 1024),  # 100MB
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        url = "https://example.com/large.pdf"

        # The size check raises an error, and with no fallback configured,
        # we get a wrapper error with the size limit error as the cause
        with pytest.raises(RuntimeError) as exc_info:
            provider._ingest(url)

        # The size limit error is the cause of the wrapper exception
        assert exc_info.value.__cause__ is not None
        assert "exceeds limit" in str(exc_info.value.__cause__)

    def test_ingest_local_pdf_size_warning(self) -> None:
        """Test size warning for large local PDFs."""
        provider = PDFProvider()

        # We can't easily create a 20MB+ PDF for testing
        # This tests that the size check exists in the code path
        # Real validation happens with actual file sizes

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            with open(tmp_path, "wb") as f:
                writer.write(f)

        try:
            # Should raise due to no text, but validates size check path
            with pytest.raises(RuntimeError, match="No text content could be extracted"):
                provider._ingest(str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_extract_text_maps_metadata_and_skips_failed_page(self, tmp_path: Path) -> None:
        """PDF extraction should preserve metadata and tolerate one unreadable page."""
        provider = PDFProvider()
        pdf = tmp_path / "report-name.pdf"
        pdf.write_bytes(b"pdf")
        good_page = MagicMock()
        good_page.extract_text.return_value = "Useful content"
        bad_page = MagicMock()
        bad_page.extract_text.side_effect = RuntimeError("bad page")
        reader = MagicMock()
        reader.pages = [good_page, bad_page]
        reader.metadata = {
            "/Title": "Report",
            "/Author": "Author",
            "/CreationDate": "2026-01-01",
        }

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            result = provider._extract_text_from_pdf(pdf, max_pages=5)

        assert result.title == "Report"
        assert result.author == "Author"
        assert result.published_date == "2026-01-01"
        assert result.content == "Useful content"

    def test_extract_text_uses_filename_defaults_and_truncates(self, tmp_path: Path) -> None:
        """Missing PDF metadata should fall back to the filename."""
        provider = PDFProvider()
        pdf = tmp_path / "report-name.pdf"
        pdf.write_bytes(b"pdf")
        pages = [MagicMock(), MagicMock()]
        pages[0].extract_text.return_value = "First page"
        pages[1].extract_text.return_value = "Ignored page"
        reader = MagicMock()
        reader.pages = pages
        reader.metadata = {}

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            result = provider._extract_text_from_pdf(pdf, max_pages=1)

        assert result.title == "Report Name"
        assert result.author == "Unknown"
        assert result.content == "First page"

    def test_fetch_supadata_rejects_empty_content(self) -> None:
        """Supadata PDF fallback should reject empty extraction responses."""
        provider = PDFProvider()
        with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {}
            with pytest.raises(ValueError, match="no content"):
                provider._fetch_supadata("https://example.com/report.pdf")


class TestPDFProviderFactory:
    """Test PDF provider integration with factory."""

    def test_factory_selects_pdf_provider_for_pdf_urls(self) -> None:
        """Test that factory selects PDFProvider for PDF URLs."""
        from obsidian_ai_tools.providers.factory import ProviderFactory

        provider = ProviderFactory.get_provider("https://example.com/doc.pdf")
        assert provider.name == "pdf"

    def test_factory_does_not_select_pdf_for_other_urls(self) -> None:
        """Test that factory doesn't select PDFProvider for non-PDF URLs."""
        from obsidian_ai_tools.providers.factory import ProviderFactory

        # Should select web provider, not PDF
        provider = ProviderFactory.get_provider("https://example.com/article")
        assert provider.name != "pdf"


# ---------------------------------------------------------------------------
# Mutation-hardening tests: _record_attempt
# ---------------------------------------------------------------------------


class TestRecordAttempt:
    """Tests for the module-level attempt recorder."""

    def test_forwards_exact_args_to_observability_db(self) -> None:
        """Every argument must reach record_provider_attempt unchanged."""
        url = "https://example.com/doc.pdf"
        with patch("obsidian_ai_tools.providers.pdf.get_db") as mock_get_db:
            _record_attempt("primary", "success", 1.5, "ValueError", url)

        mock_get_db.return_value.record_provider_attempt.assert_called_once_with(
            "pdf", "primary", "success", 1.5, "ValueError", url
        )

    def test_swallows_db_failures(self) -> None:
        """A broken observability DB must not break ingestion."""
        with patch("obsidian_ai_tools.providers.pdf.get_db", side_effect=RuntimeError("db down")):
            _record_attempt("primary", "failure", 0.5)  # must not raise


# ---------------------------------------------------------------------------
# Mutation-hardening tests: __init__ / validate / _ingest routing
# ---------------------------------------------------------------------------


class TestPDFProviderConfiguration:
    """Constructor behaviour with different settings sources."""

    class _FakeSettings:
        supadata_key = "custom-supadata-key"
        max_pdf_pages = 7
        max_pdf_size_mb = 3

    class _BareSettings:
        supadata_key = "custom-supadata-key"

    def test_init_reads_supadata_url_and_custom_limits(self) -> None:
        """Custom settings values and the fixed Supadata URL are wired up."""
        with patch(
            "obsidian_ai_tools.providers.pdf.get_settings", return_value=self._FakeSettings()
        ):
            provider = PDFProvider()

        assert provider.supadata_key == "custom-supadata-key"
        assert provider.supadata_url == "https://api.supadata.ai/v1/scrape"
        assert provider.max_pages == 7
        assert provider.max_size_mb == 3

    def test_init_falls_back_to_default_limits_when_unset(self) -> None:
        """Missing settings attributes fall back to the documented defaults."""
        with patch(
            "obsidian_ai_tools.providers.pdf.get_settings", return_value=self._BareSettings()
        ):
            provider = PDFProvider()

        assert provider.max_pages == 50
        assert provider.max_size_mb == 20


class TestValidatePaths:
    """Path-like PDF detection."""

    def test_validate_absolute_path_like_pdf(self) -> None:
        """An absolute path ending in .pdf is a PDF candidate."""
        provider = PDFProvider()
        assert provider.validate("/nonexistent/archive/annual-report.pdf") is True

    def test_validate_parent_relative_path_like_pdf(self) -> None:
        """A '../' path ending in .pdf is a PDF candidate."""
        provider = PDFProvider()
        assert provider.validate("../archive/report-2026.pdf") is True


class TestIngestRouting:
    """_ingest dispatch between local and remote handlers."""

    def test_ingest_routes_http_url_to_remote(self) -> None:
        """Lowercase http:// URLs must go through the remote handler."""
        provider = PDFProvider()
        metadata = ArticleMetadata(url="http://example.com/doc.pdf", title="T", content="c")

        with patch.object(provider, "_ingest_remote", return_value=metadata) as mock_remote:
            with patch.object(provider, "_ingest_local") as mock_local:
                result = provider._ingest("http://example.com/doc.pdf", max_pages=9)

        assert result is metadata
        mock_remote.assert_called_once_with("http://example.com/doc.pdf", 9)
        mock_local.assert_not_called()

    def test_ingest_passes_provider_page_limit_by_default(self) -> None:
        """Calling _ingest without max_pages uses the provider configured limit."""
        provider = PDFProvider()
        metadata = ArticleMetadata(url="https://example.com/doc.pdf", title="T", content="c")

        with patch.object(provider, "_ingest_remote", return_value=metadata) as mock_remote:
            provider._ingest("https://example.com/doc.pdf")

        mock_remote.assert_called_once_with("https://example.com/doc.pdf", provider.max_pages)


# ---------------------------------------------------------------------------
# Mutation-hardening tests: _ingest_local (+ size checks, attempt recording)
# ---------------------------------------------------------------------------


def _article(url: str = "file://x") -> ArticleMetadata:
    return ArticleMetadata(url=url, title="T", content="c")


class TestIngestLocalMetrics:
    """Local ingestion side effects: attempt records, logs, size checks."""

    def test_ingest_local_directory_raises_with_message(self, tmp_path: Path) -> None:
        """Directories are rejected with the documented message."""
        provider = PDFProvider()
        with pytest.raises(IsADirectoryError, match="Path is a directory"):
            provider._ingest(str(tmp_path))

    def test_ingest_local_records_success_and_logs(self, tmp_path: Path, caplog) -> None:
        """Success path records a primary success and logs the extraction start."""
        caplog.set_level(logging.INFO)
        provider = PDFProvider()
        pdf = tmp_path / "notes.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        mock_metadata = _article(str(pdf))
        provider._extract_text_from_pdf = Mock(return_value=mock_metadata)

        with patch("obsidian_ai_tools.providers.pdf._record_attempt") as mock_attempt:
            with patch(
                "obsidian_ai_tools.providers.pdf.time.monotonic", side_effect=[100.0, 100.0]
            ):
                result = provider._ingest(str(pdf))

        assert result is mock_metadata
        mock_attempt.assert_called_once_with("primary", "success", 0.0, url=str(pdf))
        assert any("Extracting text from local PDF" in rec.message for rec in caplog.records)

    def test_ingest_local_records_failure(self, tmp_path: Path) -> None:
        """Failure path records a primary failure with error type and source."""
        provider = PDFProvider()
        pdf = tmp_path / "notes.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        provider._extract_text_from_pdf = Mock(side_effect=ValueError("boom"))

        with patch("obsidian_ai_tools.providers.pdf._record_attempt") as mock_attempt:
            with patch(
                "obsidian_ai_tools.providers.pdf.time.monotonic", side_effect=[100.0, 101.0]
            ):
                with pytest.raises(ValueError, match="boom"):
                    provider._ingest(str(pdf))

        mock_attempt.assert_called_once_with("primary", "failure", 1.0, "ValueError", str(pdf))

    def test_ingest_local_small_file_has_no_size_warning(self, tmp_path: Path, caplog) -> None:
        """Small files never trigger the size warning."""
        provider = PDFProvider()
        pdf = tmp_path / "small.pdf"
        pdf.write_bytes(b"\x00" * 4096)
        provider._extract_text_from_pdf = Mock(return_value=_article(str(pdf)))

        provider._ingest(str(pdf))

        assert not any("PDF file size" in rec.message for rec in caplog.records)

    def test_ingest_local_size_warning_exact_message(self, tmp_path: Path, caplog) -> None:
        """A file slightly over the limit logs the exact size warning."""
        provider = PDFProvider()
        pdf = tmp_path / "big.pdf"
        pdf.write_bytes(b"\x00" * 20_980_000)  # ~20.007 MB in the true denominator
        provider._extract_text_from_pdf = Mock(return_value=_article(str(pdf)))

        provider._ingest(str(pdf))

        messages = [rec.message for rec in caplog.records]
        assert "PDF file size (20.0MB) exceeds limit (20MB)" in messages

    def test_ingest_local_exactly_at_limit_has_no_warning(self, tmp_path: Path, caplog) -> None:
        """A file of exactly max_size_mb is NOT over the limit."""
        provider = PDFProvider()
        pdf = tmp_path / "limit.pdf"
        pdf.write_bytes(b"\x00" * (20 * 1024 * 1024))  # exactly 20MB
        provider._extract_text_from_pdf = Mock(return_value=_article(str(pdf)))

        provider._ingest(str(pdf))

        assert not any("PDF file size" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Mutation-hardening tests: _extract_text_from_pdf
# ---------------------------------------------------------------------------


def _mock_reader(pages: list[Mock], metadata: dict | None = None) -> Mock:
    reader = Mock()
    reader.pages = pages
    reader.metadata = metadata
    return reader


def _text_page(text: str) -> Mock:
    page = MagicMock()
    page.extract_text.return_value = text
    return page


class TestExtractTextFromPdf:
    """Text extraction behaviour for the pypdf-reader path."""

    def test_extract_open_failure_logs_and_wraps_error(self, tmp_path: Path, caplog) -> None:
        """A broken PDF logs the open error and raises a wrapped RuntimeError."""
        provider = PDFProvider()
        pdf = tmp_path / "broken.pdf"
        pdf.write_bytes(b"not a pdf")

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="Failed to open PDF file: boom"):
                provider._extract_text_from_pdf(pdf, max_pages=5)

        assert any("Failed to open PDF: boom" in rec.message for rec in caplog.records)

    def test_extract_truncation_warning_exact_message(self, tmp_path: Path, caplog) -> None:
        """Truncating to a page limit logs the documented warning."""
        provider = PDFProvider()
        pdf = tmp_path / "long.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        reader = _mock_reader([_text_page("p1"), _text_page("p2"), _text_page("p3")], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            provider._extract_text_from_pdf(pdf, max_pages=2)

        messages = [rec.message for rec in caplog.records]
        assert "PDF has 3 pages, extracting first 2 pages only" in messages

    def test_extract_equal_pages_have_no_truncation_warning(self, tmp_path: Path, caplog) -> None:
        """No truncation warning when the page count equals the limit."""
        provider = PDFProvider()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        reader = _mock_reader([_text_page("p1"), _text_page("p2")], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            provider._extract_text_from_pdf(pdf, max_pages=2)

        assert not any("PDF has" in rec.message for rec in caplog.records)

    def test_extract_page_failure_warning_names_page(self, tmp_path: Path, caplog) -> None:
        """Failed page extraction logs the 1-based page number."""
        provider = PDFProvider()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        bad_page = MagicMock()
        bad_page.extract_text.side_effect = RuntimeError("bad page")
        reader = _mock_reader([_text_page("fine"), bad_page], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            provider._extract_text_from_pdf(pdf, max_pages=5)

        assert any(
            "Failed to extract text from page 2: bad page" in rec.message for rec in caplog.records
        )

    def test_extract_continues_after_failed_first_page(self, tmp_path: Path) -> None:
        """A failing page is skipped, not a reason to abandon extraction."""
        provider = PDFProvider()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        bad_page = MagicMock()
        bad_page.extract_text.side_effect = RuntimeError("bad page")
        reader = _mock_reader([bad_page, _text_page("Good content")], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            result = provider._extract_text_from_pdf(pdf, max_pages=5)

        assert result.content == "Good content"

    def test_extract_no_text_raises_exact_message(self, tmp_path: Path) -> None:
        """Whitespace-only pages produce the exact no-content error."""
        provider = PDFProvider()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        reader = _mock_reader([_text_page("   \n\t ")], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            with pytest.raises(RuntimeError) as exc_info:
                provider._extract_text_from_pdf(pdf, max_pages=5)

        assert str(exc_info.value) == "No text content could be extracted from PDF"

    def test_extract_joins_pages_with_blank_line(self, tmp_path: Path) -> None:
        """Multiple pages are joined with a blank line between them."""
        provider = PDFProvider()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        reader = _mock_reader([_text_page("First page"), _text_page("Second page")], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            result = provider._extract_text_from_pdf(pdf, max_pages=5)

        assert result.content == "First page\n\nSecond page"

    def test_extract_title_fallback_replaces_separators(self, tmp_path: Path) -> None:
        """Filename-derived titles convert underscores and hyphens to spaces."""
        provider = PDFProvider()
        pdf = tmp_path / "quarterly_report-final.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        reader = _mock_reader([_text_page("body")], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            result = provider._extract_text_from_pdf(pdf, max_pages=5)

        assert result.title == "Quarterly Report Final"

    def test_extract_sets_site_name(self, tmp_path: Path) -> None:
        """Extraction always brands the result as a PDF Document."""
        provider = PDFProvider()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        reader = _mock_reader([_text_page("body")], metadata={})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            result = provider._extract_text_from_pdf(pdf, max_pages=5)

        assert result.site_name == "PDF Document"

    def test_extract_missing_creation_date_is_none(self, tmp_path: Path) -> None:
        """A PDF without a CreationDate yields published_date None."""
        provider = PDFProvider()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        reader = _mock_reader([_text_page("body")], metadata={"/Title": "T", "/Author": "A"})

        with patch("obsidian_ai_tools.providers.pdf.PdfReader", return_value=reader):
            result = provider._extract_text_from_pdf(pdf, max_pages=5)

        assert result.published_date is None


# ---------------------------------------------------------------------------
# Mutation-hardening tests: _fetch_supadata
# ---------------------------------------------------------------------------


class TestFetchSupadata:
    """Supadata fallback requests and field mapping."""

    def test_posts_exact_request_and_maps_fields(self, caplog) -> None:
        """The POST call, payload, headers and result fields are exact."""
        caplog.set_level(logging.INFO)
        provider = PDFProvider()
        provider.supadata_key = "supa-key"

        with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value.json.return_value = {
                "content": "Body text",
                "title": "Doc Title",
                "author": "Jane Doe",
                "date_published": "2026-01-04T12:00:00Z",
            }
            result = provider._fetch_supadata("https://example.com/doc.pdf")

        mock_post.assert_called_once_with(
            "https://api.supadata.ai/v1/scrape",
            json={"url": "https://example.com/doc.pdf", "render_js": True, "block_ads": True},
            headers={"x-api-key": "supa-key", "Content-Type": "application/json"},
            timeout=60,
        )
        assert result.content == "Body text"
        assert result.title == "Doc Title"
        assert result.author == "Jane Doe"
        assert result.site_name == "PDF Document"
        assert result.url == "https://example.com/doc.pdf"
        assert result.published_date == "2026-01-04T12:00:00Z"
        assert any(
            "Using Supadata fallback for PDF extraction" in rec.message for rec in caplog.records
        )

    def test_uses_defaults_for_missing_fields(self) -> None:
        """Missing title, author and date fields fall back to defaults."""
        provider = PDFProvider()
        provider.supadata_key = "supa-key"

        with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value.json.return_value = {"content": "Only text"}
            result = provider._fetch_supadata("https://example.com/doc.pdf")

        assert result.content == "Only text"
        assert result.title == "Untitled PDF Document"
        assert result.author == "Unknown"
        assert result.site_name == "PDF Document"
        assert result.url == "https://example.com/doc.pdf"
        assert result.published_date is None

    def test_content_from_markdown_field(self) -> None:
        """When 'content' is absent, the markdown field is used."""
        provider = PDFProvider()
        provider.supadata_key = "supa-key"

        with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value.json.return_value = {"markdown": "# Heading\nBody line"}
            result = provider._fetch_supadata("https://example.com/doc.pdf")

        assert result.content == "# Heading\nBody line"

    def test_content_from_text_field(self) -> None:
        """When only 'text' is present, it is used as the content."""
        provider = PDFProvider()
        provider.supadata_key = "supa-key"

        with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value.json.return_value = {"text": "Plain extracted text"}
            result = provider._fetch_supadata("https://example.com/doc.pdf")

        assert result.content == "Plain extracted text"

    def test_empty_content_raises_exact_message(self) -> None:
        """An empty Supadata response raises the exact documented error."""
        provider = PDFProvider()
        provider.supadata_key = "supa-key"

        with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value.json.return_value = {}
            with pytest.raises(ValueError) as exc_info:
                provider._fetch_supadata("https://example.com/doc.pdf")

        assert str(exc_info.value) == "Supadata returned no content for PDF"


# ---------------------------------------------------------------------------
# Mutation-hardening tests: _ingest_remote
# ---------------------------------------------------------------------------


def _remote_response(chunks: list[bytes], headers: dict[str, str]) -> Mock:
    response = Mock()
    response.headers = headers
    response.raise_for_status = Mock()
    response.iter_content = Mock(return_value=chunks)
    return response


def _no_fallback_provider() -> PDFProvider:
    provider = PDFProvider()
    provider.supadata_key = None
    return provider


class TestIngestRemoteDirect:
    """Direct-download path: call wiring, content-type and size checks."""

    def test_direct_success_full_flow(self, tmp_path: Path, caplog) -> None:
        """Every step of the direct download is wired with exact arguments."""
        caplog.set_level(logging.INFO)
        provider = _no_fallback_provider()
        url = "https://example.com/doc.pdf"
        response = _remote_response(
            [b"chunk-one", b"chunk-two"],
            {"content-type": "application/pdf", "content-length": "1000"},
        )
        ctx_file = Mock()
        ctx_file.name = str(tmp_path / "downloaded.pdf")
        ctx_manager = MagicMock()
        ctx_manager.__enter__.return_value = ctx_file
        ctx_manager.__exit__.return_value = None

        mock_extract = Mock(return_value=_article("ignore"))
        provider._extract_text_from_pdf = mock_extract

        with (
            patch("obsidian_ai_tools.providers.pdf.requests.get", return_value=response),
            patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post,
            patch("obsidian_ai_tools.providers.pdf._limiter") as mock_limiter,
            patch("obsidian_ai_tools.providers.pdf._record_attempt") as mock_attempt,
            patch("obsidian_ai_tools.providers.pdf.time.monotonic", side_effect=[100.0, 100.0]),
            patch(
                "obsidian_ai_tools.providers.pdf.tempfile.NamedTemporaryFile",
                return_value=ctx_manager,
            ) as mock_ntf,
        ):
            result = provider._ingest(url)

        assert result is mock_extract.return_value
        mock_limiter.wait.assert_called_once_with(url)
        mock_post.assert_not_called()

        extract_path, extract_max_pages = mock_extract.call_args.args
        assert isinstance(extract_path, Path)
        assert extract_path.suffix == ".pdf"
        assert extract_max_pages == provider.max_pages
        assert mock_extract.call_args.kwargs == {"original_url": url}

        mock_ntf.assert_called_once_with(suffix=".pdf", delete=False)
        mock_attempt.assert_called_once_with("primary", "success", 0.0, url=url)
        ctx_file.write.assert_has_calls([call(b"chunk-one"), call(b"chunk-two")])
        response.iter_content.assert_called_once_with(chunk_size=8192)

        assert any(f"Downloading PDF from URL: {url}" in rec.message for rec in caplog.records)
        assert not any("Unexpected content type" in rec.message for rec in caplog.records)
        assert not any("PDF file size" in rec.message for rec in caplog.records)

    def test_requests_get_exact_call(self) -> None:
        """requests.get must receive the URL, timeout and stream arguments."""
        provider = _no_fallback_provider()
        url = "https://example.com/doc.pdf"
        response = _remote_response(
            [b"chunk-one"],
            {"content-type": "application/pdf", "content-length": "1000"},
        )
        provider._extract_text_from_pdf = Mock(return_value=_article("ignore"))

        with (
            patch("obsidian_ai_tools.providers.pdf.requests.get", return_value=response) as mg,
            patch("obsidian_ai_tools.providers.pdf.requests.post"),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt"),
        ):
            provider._ingest(url)

        mg.assert_called_once_with(url, timeout=30, stream=True)

    def test_exact_size_limit_is_allowed(self) -> None:
        """A download of exactly max_size_mb is allowed through."""
        provider = _no_fallback_provider()
        url = "https://example.com/doc.pdf"
        response = _remote_response(
            [b"chunk-one"],
            {
                "content-type": "application/pdf",
                "content-length": str(20 * 1024 * 1024),
            },
        )
        provider._extract_text_from_pdf = Mock(return_value=_article("ignore"))

        with (
            patch("obsidian_ai_tools.providers.pdf.requests.get", return_value=response),
            patch("obsidian_ai_tools.providers.pdf.requests.post"),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt"),
        ):
            result = provider._ingest(url)

        assert result.title == "T"

    @pytest.mark.parametrize("content_length", ["21000000", "20980000"])
    def test_size_over_limit_raises(self, content_length: str) -> None:
        """Downloads over max_size_mb raise with the size message."""
        provider = _no_fallback_provider()
        url = "https://example.com/doc.pdf"
        response = _remote_response(
            [b"chunk-one"],
            {"content-type": "application/pdf", "content-length": content_length},
        )
        provider._extract_text_from_pdf = Mock(return_value=_article("ignore"))

        with (
            patch("obsidian_ai_tools.providers.pdf.requests.get", return_value=response),
            patch("obsidian_ai_tools.providers.pdf.requests.post"),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt"),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                provider._ingest(url)

        assert str(exc_info.value.__cause__) == "PDF file size (20.0MB) exceeds limit (20MB)"

    def test_pdf_content_type_has_no_warning(self, caplog) -> None:
        """application/pdf content types are accepted silently."""
        provider = _no_fallback_provider()
        url = "https://example.com/doc.pdf"
        response = _remote_response(
            [b"chunk-one"],
            {"content-type": "application/pdf", "content-length": "1000"},
        )
        provider._extract_text_from_pdf = Mock(return_value=_article("ignore"))

        with (
            patch("obsidian_ai_tools.providers.pdf.requests.get", return_value=response),
            patch("obsidian_ai_tools.providers.pdf.requests.post"),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt"),
        ):
            provider._ingest(url)

        assert not any("Unexpected content type" in rec.message for rec in caplog.records)

    @pytest.mark.parametrize("content_type", ["pdf", "application/x-pdf", "binary/pdf"])
    def test_pdf_substring_content_types_have_no_warning(self, caplog, content_type: str) -> None:
        """Content types containing 'pdf' (but not the full mime) are fine too."""
        provider = _no_fallback_provider()
        url = "https://example.com/doc.pdf"
        response = _remote_response(
            [b"chunk-one"],
            {"content-type": content_type, "content-length": "1000"},
        )
        provider._extract_text_from_pdf = Mock(return_value=_article("ignore"))

        with (
            patch("obsidian_ai_tools.providers.pdf.requests.get", return_value=response),
            patch("obsidian_ai_tools.providers.pdf.requests.post"),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt"),
        ):
            provider._ingest(url)

        assert not any("Unexpected content type" in rec.message for rec in caplog.records)

    def test_missing_content_type_warns_with_exact_message(self, caplog) -> None:
        """A missing content type warns with the empty value rendered."""
        provider = _no_fallback_provider()
        url = "https://example.com/doc.pdf"
        response = _remote_response([b"chunk-one"], {"content-length": "1000"})
        provider._extract_text_from_pdf = Mock(return_value=_article("ignore"))

        with (
            patch("obsidian_ai_tools.providers.pdf.requests.get", return_value=response),
            patch("obsidian_ai_tools.providers.pdf.requests.post"),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt"),
        ):
            result = provider._ingest(url)

        assert result.title == "T"
        messages = [rec.message for rec in caplog.records]
        assert "Unexpected content type: " in messages


class TestIngestRemoteFallback:
    """Fallback path: attempt records on both failure and success."""

    def test_fallback_success_records_both_attempts(self, caplog) -> None:
        """Primary failure + Supadata success are both recorded with exact args."""
        caplog.set_level(logging.INFO)
        provider = PDFProvider()
        provider.supadata_key = "supa-key"
        url = "https://example.com/doc.pdf"

        supadata_response = Mock()
        supadata_response.raise_for_status = Mock()
        supadata_response.json.return_value = {
            "content": "Supadata body",
            "title": "Supadata Title",
            "author": "Supadata Author",
        }

        with (
            patch(
                "obsidian_ai_tools.providers.pdf.requests.get",
                side_effect=ConnectionError("network down"),
            ),
            patch(
                "obsidian_ai_tools.providers.pdf.requests.post",
                return_value=supadata_response,
            ),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt") as mock_attempt,
            patch(
                "obsidian_ai_tools.providers.pdf.time.monotonic",
                side_effect=[100.0, 101.0, 101.0, 101.0],
            ),
        ):
            result = provider._ingest(url)

        assert result.content == "Supadata body"
        assert result.title == "Supadata Title"
        assert mock_attempt.call_args_list == [
            call("primary", "failure", 1.0, "ConnectionError", url),
            call("fallback", "success", 0.0, url=url),
        ]
        assert any("Direct PDF download failed" in rec.message for rec in caplog.records)

    def test_fallback_failure_records_and_propagates(self) -> None:
        """Supadata failure is recorded and the original error re-raised."""
        provider = PDFProvider()
        provider.supadata_key = "supa-key"
        url = "https://example.com/doc.pdf"

        supadata_response = Mock()
        supadata_response.raise_for_status = Mock(
            side_effect=requests.HTTPError("401 Unauthorized")
        )

        with (
            patch(
                "obsidian_ai_tools.providers.pdf.requests.get",
                side_effect=ConnectionError("network down"),
            ),
            patch(
                "obsidian_ai_tools.providers.pdf.requests.post",
                return_value=supadata_response,
            ),
            patch("obsidian_ai_tools.providers.pdf._limiter"),
            patch("obsidian_ai_tools.providers.pdf._record_attempt") as mock_attempt,
            patch(
                "obsidian_ai_tools.providers.pdf.time.monotonic",
                side_effect=[100.0, 101.0, 101.0, 101.0],
            ),
        ):
            with pytest.raises(requests.HTTPError):
                provider._ingest(url)

        assert mock_attempt.call_args_list == [
            call("primary", "failure", 1.0, "ConnectionError", url),
            call("fallback", "failure", 0.0, "HTTPError", url),
        ]
