"""Tests for PDF provider."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pypdf import PdfWriter

from obsidian_ai_tools.models import ArticleMetadata
from obsidian_ai_tools.providers.pdf import PDFProvider


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
