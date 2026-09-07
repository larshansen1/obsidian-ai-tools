"""X (Twitter) thread ingestion provider.

Primary path: browser-captured thread text sent by the Chrome extension
(chrome-extension/capture.js). Fallback: Supadata metadata endpoint
(GET /v1/metadata) for URL-only ingests (CLI / reading list).

Link-only posts (X Articles / Longform, whose entire text is a bare URL)
resolve the link and fetch the target page's text via WebProvider.

Verified against docs.supadata.ai 2026-09: there is no
tweet/thread text endpoint; /v1/metadata returns the post's own text
(description/title), author handle, createdAt and engagement stats for a
single post (1 credit). /v1/transcript only covers X *video* posts.
"""

import logging
import re
import time
from typing import Any, cast

import requests

from ..config import get_settings
from ..models import ArticleMetadata, ThreadMetadata, ThreadTweet
from . import _limiter, _record_attempt
from .base import BaseProvider
from .web import WebProvider

logger = logging.getLogger(__name__)

# Matches the separator used by chrome-extension/capture.js when joining tweets.
_TWEET_SEPARATOR = "\n\n---\n\n"

_STATUS_RE = re.compile(
    r"^https?://(?:www\.)?(?:x\.com|twitter\.com)/[^/]+/status/\d+", re.IGNORECASE
)

# Content that is exactly one bare URL (e.g. the t.co link on an X
# Article/Longform post) carries no usable text; the link must be expanded.
_LINK_ONLY_RE = re.compile(r"^https?://\S+$")


class XThreadProvider(BaseProvider):
    """Provider for X (Twitter) threads."""

    def __init__(self) -> None:
        """Read Supadata config for the URL-only fallback path."""
        settings = get_settings()
        self.supadata_key = settings.supadata_key
        self.supadata_url = "https://api.supadata.ai/v1/metadata"

    @property
    def name(self) -> str:
        return "x"

    def validate(self, source: str) -> bool:
        """True for x.com/twitter.com status URLs, False otherwise."""
        return _STATUS_RE.match(source.strip()) is not None

    def _ingest(self, source: str, **kwargs: Any) -> ThreadMetadata:
        """Build thread metadata from captured content or Supadata fallback."""
        captured_content = kwargs.get("captured_content")
        if isinstance(captured_content, str) and captured_content.strip():
            _t0 = time.monotonic()
            _record_attempt("x", "extension", "success", time.monotonic() - _t0, url=source)
            return self._from_capture(source, kwargs)

        # Enforce rate limit before any network call
        _limiter.wait(source)

        if self.supadata_key:
            _t1 = time.monotonic()
            try:
                metadata = self._fetch_supadata(source)
                _record_attempt("x", "supadata", "success", time.monotonic() - _t1, url=source)
                return metadata
            except Exception as exc:
                logger.error(f"Supadata X fetch failed: {exc}")
                _record_attempt(
                    "x", "supadata", "failure", time.monotonic() - _t1, type(exc).__name__, source
                )
                raise RuntimeError(f"Failed to fetch X thread from {source}: {exc}") from exc

        raise RuntimeError(f"Failed to fetch X thread from {source} and no fallback configured")

    def _from_capture(self, source: str, kwargs: dict[str, Any]) -> ThreadMetadata:
        """Build ThreadMetadata from extension-captured content (no network)."""
        content = cast(str, kwargs.get("captured_content")).strip()
        author = kwargs.get("captured_author")
        title = kwargs.get("captured_title") or (f"@{author} on X" if author else "X Thread")

        if _LINK_ONLY_RE.match(content):
            return self._expand_link_only(content, source, fallback_author=author)

        tweets = [
            ThreadTweet(text=part.strip())
            for part in content.split(_TWEET_SEPARATOR)
            if part.strip()
        ]
        return ThreadMetadata(
            content=content,
            title=title,
            author=author,
            published_date=kwargs.get("captured_date"),
            site_name="X",
            url=source,
            tweets=tweets,
            fetch_method="extension",
        )

    def _fetch_supadata(self, url: str) -> ThreadMetadata:
        """Fetch a post's metadata from the Supadata /v1/metadata endpoint."""
        api_key = cast(str, self.supadata_key)
        response = requests.get(
            self.supadata_url, headers={"x-api-key": api_key}, params={"url": url}, timeout=30
        )
        response.raise_for_status()

        data = response.json()
        content = data.get("description") or data.get("title")
        if not content:
            raise ValueError("Supadata returned no text content")

        author_info = data.get("author") or {}
        author = author_info.get("username") or author_info.get("displayName")

        if _LINK_ONLY_RE.match(content.strip()):
            return self._expand_link_only(content.strip(), url, fallback_author=author)

        stats: dict[str, int] | None = None
        raw_stats = data.get("stats") or {}
        if any(value is not None for value in raw_stats.values()):
            stats = {k: v for k, v in raw_stats.items() if v is not None}

        media = data.get("media") or {}
        media_type = media.get("type")
        media_presence = (
            f"{media_type} attachment" if media_type in ("video", "image", "carousel") else None
        )

        return ThreadMetadata(
            content=content,
            title=data.get("title") or (f"@{author} on X" if author else "X Post"),
            author=author,
            published_date=data.get("createdAt"),
            site_name="X",
            url=url,
            tweets=[
                ThreadTweet(text=content, timestamp=data.get("createdAt"), tweet_id=data.get("id"))
            ],
            engagement=stats,
            media_presence=media_presence,
            fetch_method="supadata",
        )

    def _expand_link_only(
        self, link: str, source: str, fallback_author: str | None = None
    ) -> ThreadMetadata:
        """Resolve a bare link and fetch the target page via WebProvider.

        The resolved target (e.g. an x.com/i/article page) is fetched with
        the repo's existing resilient WebProvider pipeline (trafilatura with
        a Supadata scrape fallback); ``BaseProvider.ingest`` never calls
        ``validate()``, so WebProvider accepts x.com targets here.
        """
        resolved_url = link
        _t0 = time.monotonic()
        try:
            _limiter.wait(link)
            response = requests.get(link, allow_redirects=True, stream=True, timeout=30)
            resolved_url = response.url
            response.raise_for_status()
            response.close()
            article = cast(ArticleMetadata, WebProvider().ingest(resolved_url))
        except Exception as exc:
            _record_attempt(
                "x", "expand", "failure", time.monotonic() - _t0, type(exc).__name__, source
            )
            raise RuntimeError(
                f"Failed to fetch X article content from {resolved_url}: {exc}"
            ) from exc
        _record_attempt("x", "expand", "success", time.monotonic() - _t0, url=source)
        return ThreadMetadata(
            content=article.content,
            title=article.title,
            author=fallback_author or article.author,
            url=source,
            site_name="X",
            tweets=[ThreadTweet(text=article.content)],
            fetch_method="web-expand",
        )
