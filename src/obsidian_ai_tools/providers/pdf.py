"""PDF document ingestion provider."""

import logging
import tempfile
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader

from ..config import get_settings
from ..models import ArticleMetadata
from ..utils.rate_limiter import RateLimiter
from .base import BaseProvider

logger = logging.getLogger(__name__)

# Global rate limiter to share state across instances
_limiter = RateLimiter(delay=2.0)


class PDFProvider(BaseProvider):
    """Provider for extracting text content from PDF documents.

    Supports both local files and remote URLs. For remote URLs,
    downloads the PDF first, then extracts text. Falls back to
    Supadata if direct download fails.
    """

    def __init__(self) -> None:
        """Initialize provider with configuration."""
        settings = get_settings()
        self.supadata_key = settings.supadata_key
        self.supadata_url = "https://api.supadata.ai/v1/scrape"
        self.max_pages = getattr(settings, "max_pdf_pages", 50)
        self.max_size_mb = getattr(settings, "max_pdf_size_mb", 20)

    @property
    def name(self) -> str:
        return "pdf"

    def validate(self, source: str) -> bool:
        """Check if source is a valid PDF file or URL.

        Args:
            source: Input URL or file path

        Returns:
            True if source appears to be a PDF
        """
        lower_source = source.lower()

        # Check if it's a local file
        path = Path(source)
        if path.exists():
            return path.suffix.lower() == ".pdf"

        # Check if it's a URL ending in .pdf
        if source.startswith(("http://", "https://")):
            return lower_source.endswith(".pdf")

        # Check path-like patterns that might be PDFs
        return source.startswith(("/", "./", "../")) and lower_source.endswith(".pdf")

    def _ingest(self, source: str, max_pages: int | None = None, **kwargs: Any) -> ArticleMetadata:
        """Extract text content from PDF.

        Args:
            source: Path to PDF file or URL
            max_pages: Optional override for maximum pages to process
            **kwargs: Additional arguments

        Returns:
            ArticleMetadata object with extracted content

        Raises:
            FileNotFoundError: If local file doesn't exist
            RuntimeError: If PDF extraction fails
        """
        # Use provided max_pages or default from config
        page_limit = max_pages if max_pages is not None else self.max_pages

        # Determine if source is local or remote
        if source.startswith(("http://", "https://")):
            return self._ingest_remote(source, page_limit)
        else:
            return self._ingest_local(source, page_limit)

    def _ingest_local(self, file_path: str, max_pages: int) -> ArticleMetadata:
        """Extract text from a local PDF file.

        Args:
            file_path: Path to local PDF file
            max_pages: Maximum pages to extract

        Returns:
            ArticleMetadata object
        """
        path = Path(file_path).resolve()

        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        if not path.is_file():
            raise IsADirectoryError(f"Path is a directory: {path}")

        # Check file size
        file_size_mb = path.stat().st_size / (1024 * 1024)
        if file_size_mb > self.max_size_mb:
            logger.warning(
                f"PDF file size ({file_size_mb:.1f}MB) exceeds limit ({self.max_size_mb}MB)"
            )

        logger.info(f"Extracting text from local PDF: {path}")

        return self._extract_text_from_pdf(path, max_pages)

    def _ingest_remote(self, url: str, max_pages: int) -> ArticleMetadata:
        """Extract text from a remote PDF URL.

        Args:
            url: URL to PDF file
            max_pages: Maximum pages to extract

        Returns:
            ArticleMetadata object
        """
        # Enforce rate limit
        _limiter.wait(url)

        # Try direct download first
        try:
            logger.info(f"Downloading PDF from URL: {url}")
            response = requests.get(url, timeout=30, stream=True)
            response.raise_for_status()

            # Check content type
            content_type = response.headers.get("content-type", "").lower()
            if "application/pdf" not in content_type and "pdf" not in content_type:
                logger.warning(f"Unexpected content type: {content_type}")

            # Check file size
            content_length = response.headers.get("content-length")
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > self.max_size_mb:
                    raise RuntimeError(
                        f"PDF file size ({size_mb:.1f}MB) exceeds limit ({self.max_size_mb}MB)"
                    )

            # Download to temporary file
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                tmp_path = Path(tmp_file.name)

                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)

            try:
                # Extract text from downloaded PDF
                result = self._extract_text_from_pdf(tmp_path, max_pages, original_url=url)
                return result
            finally:
                # Clean up temporary file
                tmp_path.unlink(missing_ok=True)

        except Exception as e:
            logger.warning(f"Direct PDF download failed: {e}. Attempting fallback.")

            # Fall back to Supadata
            if self.supadata_key:
                return self._fetch_supadata(url)
            else:
                raise RuntimeError(
                    f"Failed to download PDF from {url} and no fallback configured"
                ) from e

    def _extract_text_from_pdf(
        self, pdf_path: Path, max_pages: int, original_url: str | None = None
    ) -> ArticleMetadata:
        """Extract text content from a PDF file.

        Args:
            pdf_path: Path to PDF file
            max_pages: Maximum pages to extract
            original_url: Original URL if downloaded from remote

        Returns:
            ArticleMetadata object
        """
        try:
            reader = PdfReader(str(pdf_path))
        except Exception as e:
            logger.error(f"Failed to open PDF: {e}")
            raise RuntimeError(f"Failed to open PDF file: {e}") from e

        total_pages = len(reader.pages)
        pages_to_extract = min(total_pages, max_pages)

        # Log truncation warning
        truncated = total_pages > max_pages
        if truncated:
            logger.warning(
                f"PDF has {total_pages} pages, extracting first {pages_to_extract} pages only"
            )

        # Extract metadata
        metadata: dict[str, Any] = reader.metadata or {}
        pdf_title = metadata.get("/Title", "")
        pdf_author = metadata.get("/Author", "")
        pdf_creation_date = metadata.get("/CreationDate", "")

        # Extract text from pages
        text_parts = []
        for i in range(pages_to_extract):
            try:
                page = reader.pages[i]
                text = page.extract_text()
                if text.strip():
                    text_parts.append(text)
            except Exception as e:
                logger.warning(f"Failed to extract text from page {i + 1}: {e}")
                continue

        if not text_parts:
            raise RuntimeError("No text content could be extracted from PDF")

        content = "\n\n".join(text_parts)

        # Determine title
        if pdf_title:
            title = pdf_title
        else:
            title = pdf_path.stem.replace("_", " ").replace("-", " ").title()

        # Determine author
        author = pdf_author if pdf_author else "Unknown"

        # Determine URL
        url = original_url if original_url else f"file://{pdf_path}"

        # Store truncation info in content metadata (for later display)
        if truncated:
            pass
        else:
            pass

        return ArticleMetadata(
            title=title,
            url=url,
            author=author,
            site_name="PDF Document",
            content=content,
            published_date=pdf_creation_date if pdf_creation_date else None,
        )

    def _fetch_supadata(self, url: str) -> ArticleMetadata:
        """Fetch PDF content using Supadata API as fallback.

        Args:
            url: URL to PDF file

        Returns:
            ArticleMetadata object
        """
        logger.info("Using Supadata fallback for PDF extraction")

        headers = {"x-api-key": self.supadata_key, "Content-Type": "application/json"}

        payload = {
            "url": url,
            "render_js": True,
            "block_ads": True,
        }

        response = requests.post(self.supadata_url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()

        data = response.json()

        content = data.get("content") or data.get("markdown") or data.get("text")

        if not content:
            raise ValueError("Supadata returned no content for PDF")

        return ArticleMetadata(
            content=content,
            title=data.get("title") or "Untitled PDF Document",
            author=data.get("author") or "Unknown",
            site_name="PDF Document",
            url=url,
            published_date=data.get("date_published"),
        )
