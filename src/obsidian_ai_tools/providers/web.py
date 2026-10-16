"""Web content provider implementation using Trafilatura with Supadata fallback."""

import logging
import re
import time
from typing import Any, cast
from urllib.parse import urlparse

import requests
import trafilatura
from trafilatura.settings import use_config

from ..config import get_settings
from ..models import ArticleMetadata
from . import _limiter, _record_attempt
from .base import BaseProvider

logger = logging.getLogger(__name__)

JS_RENDERED_MESSAGE = (
    "The page loads its article with JavaScript, so kai could not read it. "
    "Use the Chrome extension to capture it."
)

# A whole text node that is only a loading placeholder, e.g. "Loading Blog Post...".
# Kept narrow (no "Loading comments...") so long articles with lazy widgets pass.
_LOADING_PLACEHOLDER_RE = re.compile(
    r">\s*(Loading(?:\s+(?:blog\s+)?(?:post|article|content|story|page))?\s*(?:\.{3}|\u2026))\s*<",
    re.IGNORECASE,
)
# Below this the extracted text is too short to be the article a placeholder stands in for.
_THIN_TEXT_CHARS = 1500

BOT_CHECK_MESSAGE = (
    "The site showed a bot check instead of the article. "
    "Open it in your browser and use the Chrome extension to capture it."
)

# Phrases that bot-check, browser-verification and access-denied stubs show instead of
# the article (Cloudflare, reCAPTCHA, Akamai, PMC). Only checked on thin pages, so a
# real article that mentions one of them is not rejected.
_BOT_CHECK_RE = re.compile(
    r"checking (?:your browser|if the site connection is secure)"
    r"|browser verification"
    r"|verify(?:ing)? (?:that )?you are (?:a )?human"
    r"|are you a robot"
    r"|just a moment\.\.\."
    r"|attention required!"
    r"|enable javascript and cookies to continue"
    r"|complete the security check"
    r"|you don't have permission to access",
    re.IGNORECASE,
)


class JavaScriptRenderedPageError(ValueError):
    """The fetched HTML is an empty shell whose article loads with JavaScript.

    A ValueError so BaseProvider.ingest does not retry: refetching returns the same shell.
    """

    def __init__(self) -> None:
        super().__init__(JS_RENDERED_MESSAGE)


def find_js_placeholder(html: str, text: str) -> str | None:
    """Return the loading placeholder if the extraction looks like a JS-rendered shell."""
    if len(text) >= _THIN_TEXT_CHARS:
        return None
    match = _LOADING_PLACEHOLDER_RE.search(html)
    return match.group(1) if match else None


class BotCheckPageError(ValueError):
    """The fetched page is a bot check or verification stub, not the article.

    A ValueError so BaseProvider.ingest does not retry: refetching hits the same check.
    """

    def __init__(self) -> None:
        super().__init__(BOT_CHECK_MESSAGE)


def find_bot_check(title: str, text: str) -> str | None:
    """Return the bot-check phrase if the page is an interstitial instead of an article."""
    if len(text) >= _THIN_TEXT_CHARS:
        return None
    match = _BOT_CHECK_RE.search(f"{title}\n{text}")
    return match.group(0) if match else None


def _reject_bot_check(url: str, title: str, text: str) -> None:
    """Raise BotCheckPageError, logging the URL and matched phrase, for an interstitial."""
    phrase = find_bot_check(title, text)
    if phrase:
        logger.warning(f"Bot-check page detected for {url}: phrase {phrase!r}")
        raise BotCheckPageError()


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
        host = urlparse(source).hostname or ""
        return (
            source.startswith(("http://", "https://"))
            and "youtube.com" not in source
            and "youtu.be" not in source
            and host not in ("x.com", "twitter.com")
            and not host.endswith((".x.com", ".twitter.com"))
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
        captured_content = kwargs.get("captured_content")
        if isinstance(captured_content, str) and captured_content.strip():
            _t0 = time.monotonic()
            _record_attempt("web", "captured", "success", time.monotonic() - _t0, url=source)
            return ArticleMetadata(
                content=captured_content.strip(),
                title=kwargs.get("captured_title") or "Captured Chat",
                author=kwargs.get("captured_author") or "Unknown Author",
                # pragma: no mutate - removing "published_date=None" is equivalent
                # (ArticleMetadata.published_date defaults to None); assert instead on
                # the resolved value (result.published_date is None) in tests.
                published_date=kwargs.get("captured_date"),
                site_name="Browser Capture",
                url=source,
            )

        # Enforce rate limit
        _limiter.wait(source)

        # 1. Check for raw content (GitHub, etc.)
        _t0 = time.monotonic()
        raw_result = self._check_raw_content(source)
        if raw_result:
            _record_attempt("web", "raw", "success", time.monotonic() - _t0, url=source)
            return ArticleMetadata(**raw_result)

        # 2. Try direct extraction (Trafilatura), on the browser-rendered HTML when the
        # extension sent it, so pages that load their article with JavaScript still work.
        captured_html = kwargs.get("captured_html")
        page_error: JavaScriptRenderedPageError | BotCheckPageError | None = None
        _t0 = time.monotonic()
        try:
            result = self._fetch_direct(
                source, html=captured_html if isinstance(captured_html, str) else None
            )
            if result:
                logger.info("Successfully fetched article using Trafilatura")
                _record_attempt("web", "primary", "success", time.monotonic() - _t0, url=source)
                return ArticleMetadata(**result)
            _record_attempt("web", "primary", "failure", time.monotonic() - _t0, url=source)
        except (JavaScriptRenderedPageError, BotCheckPageError) as e:
            page_error = e
            _record_attempt(
                "web", "primary", "failure", time.monotonic() - _t0, type(e).__name__, source
            )
        except Exception as e:
            logger.warning(f"Direct extraction failed: {e}. Attempting fallback.")
            _record_attempt(
                "web", "primary", "failure", time.monotonic() - _t0, type(e).__name__, source
            )

        # 3. Fallback to Supadata
        if self.supadata_key:
            logger.info("Falling back to Supadata extraction")
            _t1 = time.monotonic()
            try:
                result = self._fetch_supadata(source)
                if result:
                    logger.info("Successfully fetched article using Supadata")
                    _record_attempt(
                        "web", "fallback", "success", time.monotonic() - _t1, url=source
                    )
                    return ArticleMetadata(**result)
            except BotCheckPageError as e:
                _record_attempt(
                    "web", "fallback", "failure", time.monotonic() - _t1, type(e).__name__, source
                )
                raise
            except Exception as e:
                logger.error(f"Supadata extraction failed: {e}")
                _record_attempt(
                    "web", "fallback", "failure", time.monotonic() - _t1, type(e).__name__, source
                )
                raise RuntimeError(f"Failed to fetch article from {source}: {e}") from e

        if page_error is not None:
            raise page_error
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

    def _fetch_direct(self, url: str, html: str | None = None) -> dict[str, Any] | None:
        """Extract with Trafilatura from ``html``, or from a fresh download of ``url``."""
        downloaded = html if html else trafilatura.fetch_url(url)
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

        placeholder = find_js_placeholder(downloaded, data["text"])
        if placeholder:
            logger.warning(
                f"JavaScript-rendered page detected for {url}: placeholder {placeholder!r}"
            )
            raise JavaScriptRenderedPageError()
        _reject_bot_check(url, data.get("title") or "", data["text"])

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
        api_key = cast(str, self.supadata_key)
        headers = {"x-api-key": api_key}

        params = {
            "url": url,
        }

        response = requests.get(self.supadata_url, headers=headers, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()

        content = data.get("content")

        if not content:
            raise ValueError("Supadata returned no content")
        _reject_bot_check(url, data.get("name") or "", content)

        return {
            "content": content,
            "title": data.get("name") or "Untitled Web Page",
            "author": "Unknown Author",
            "date": None,
            "site_name": data.get("description") or "Web Source",
            "url": url,
        }
