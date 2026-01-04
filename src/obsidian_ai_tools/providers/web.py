"""Web content provider implementation using Trafilatura with Supadata fallback."""

import logging
from typing import Any

import requests
import trafilatura
from trafilatura.settings import use_config

from ..config import get_settings
from ..models import ArticleMetadata
from ..utils.rate_limiter import RateLimiter
from .base import BaseProvider

logger = logging.getLogger(__name__)

# Global rate limiter to share state across instances
_limiter = RateLimiter(delay=2.0)


class WebProvider(BaseProvider):
    """Provider for fetching and extracting content from web pages.

    Uses Trafilatura for direct extraction, falling back to Supadata
    if extraction fails or is blocked.
    """

    def __init__(self) -> None:
        """Initialize provider with configuration."""
        self.config = use_config()
        # Set user agent to avoid basic blocking
        self.config.set("DEFAULT", "USER_AGENT", "Mozilla/5.0 (compatible; ObsidianAI/1.0)")

        settings = get_settings()
        self.supadata_key = settings.supadata_key
        self.supadata_url = "https://api.supadata.ai/v1/web/scrape"

    @property
    def name(self) -> str:
        return "web"

    def validate(self, source: str) -> bool:
        """Check if source is a valid web URL."""
        return (
            source.startswith(("http://", "https://"))
            and "youtube.com" not in source
            and "youtu.be" not in source
        )

    def _ingest(self, source: str, **kwargs: Any) -> ArticleMetadata:
        """Fetch article content and metadata from URL.

        Args:
            source: URL to fetch content from

        Returns:
            ArticleMetadata object

        Raises:
            RuntimeError: If fetching fails with all methods
        """
        # Enforce rate limit
        _limiter.wait(source)

        # 1. Check for raw content (GitHub, etc.)
        raw_result = self._check_raw_content(source)
        if raw_result:
            return ArticleMetadata(**raw_result)

        # 2. Try direct extraction (Trafilatura)
        try:
            result = self._fetch_direct(source)
            if result:
                logger.info("Successfully fetched article using Trafilatura")
                return ArticleMetadata(**result)
        except Exception as e:
            logger.warning(f"Direct extraction failed: {e}. Attempting fallback.")

        # 3. Fallback to Supadata
        if self.supadata_key:
            logger.info("Falling back to Supadata extraction")
            try:
                result = self._fetch_supadata(source)
                if result:
                    logger.info("Successfully fetched article using Supadata")
                    return ArticleMetadata(**result)
            except Exception as e:
                logger.error(f"Supadata extraction failed: {e}")
                raise RuntimeError(f"Failed to fetch article from {source}: {e}") from e

        raise RuntimeError(f"Failed to fetch content from {source} and no fallback configured")

    def _check_raw_content(self, url: str) -> dict[str, Any] | None:
        """Check for and fetch raw content (GitHub blob, raw files)."""
        lower_url = url.lower()

        # Convert GitHub blob URLs to raw
        if "github.com" in lower_url and "/blob/" in lower_url:
            raw_url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
            logger.info(f"Converting GitHub blob URL to raw: {raw_url}")
            try:
                return self._fetch_raw(raw_url)
            except Exception as e:
                logger.warning(
                    f"GitHub raw fetch failed: {e}. Falling back to standard extraction."
                )
                return None

        # Check for already-raw content
        if (
            lower_url.endswith((".md", ".markdown", ".txt"))
            or "raw.githubusercontent.com" in lower_url
        ):
            logger.info("Detected raw content URL, attempting direct fetch")
            try:
                return self._fetch_raw(url)
            except Exception as e:
                logger.warning(f"Raw fetch failed: {e}. Falling back to standard extraction.")
                return None

        return None

    def _fetch_raw(self, url: str) -> dict[str, Any]:
        """Fetch raw content directly."""
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        content = response.text
        if not content.strip():
            raise ValueError("Empty content received")

        return {
            "content": content,
            "title": url.split("/")[-1] or "Raw Content",
            "author": "Unknown",
            "date": None,
            "site_name": "Raw Source",
            "url": url,
        }

    def _fetch_direct(self, url: str) -> dict[str, Any] | None:
        """Fetch using Trafilatura."""
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning(f"Trafilatura failed to download {url}")
            return None

        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            output_format="json",
            with_metadata=True,
        )

        if not extracted:
            logger.warning("Trafilatura failed to extract content")
            return None

        import json

        data = json.loads(extracted)

        # Ensure we have content
        if not data.get("text"):
            logger.warning("No text content extracted")
            return None

        return {
            "content": data.get("text"),
            "title": data.get("title") or "Untitled Web Page",
            "author": data.get("author") or "Unknown Author",
            "date": data.get("date"),
            "site_name": data.get("sitename") or data.get("hostname") or "Web Source",
            "url": url,
        }

    def _fetch_supadata(self, url: str) -> dict[str, Any]:
        """Fetch using Supadata API."""
        headers = {"x-api-key": self.supadata_key}

        params = {
            "url": url,
        }

        response = requests.get(self.supadata_url, headers=headers, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()

        content = data.get("content")

        if not content:
            raise ValueError("Supadata returned no content")

        return {
            "content": content,
            "title": data.get("name") or "Untitled Web Page",
            "author": "Unknown Author",
            "date": None,
            "site_name": data.get("description") or "Web Source",
            "url": url,
        }
