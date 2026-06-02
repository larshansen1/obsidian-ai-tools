"""YouTube content provider."""

import time
from typing import Any

from ..models import VideoMetadata
from ..observability import get_db
from .base import BaseProvider


class YouTubeProvider(BaseProvider):
    """Provider for YouTube videos."""

    @property
    def name(self) -> str:
        return "youtube"

    def validate(self, source: str) -> bool:
        """Check if source is a valid YouTube URL."""
        return "youtube.com" in source or "youtu.be" in source

    def _ingest(self, source: str, **kwargs: Any) -> VideoMetadata:
        """Fetch video metadata and transcript.

        Args:
            source: YouTube URL
            **kwargs: Optional arguments including:
                - provider_order: Comma-separated provider order override

        Returns:
            VideoMetadata object
        """
        from ..youtube import YouTubeClient

        client = YouTubeClient()
        _t0 = time.monotonic()
        try:
            result = client.get_video_metadata(source, provider_order=kwargs.get("provider_order"))
            try:
                get_db().record_provider_attempt(
                    "youtube", "primary", "success", time.monotonic() - _t0, url=source
                )
            except Exception:  # nosec B110
                pass
            return result
        except Exception as exc:
            try:
                get_db().record_provider_attempt(
                    "youtube",
                    "primary",
                    "failure",
                    time.monotonic() - _t0,
                    type(exc).__name__,
                    source,
                )
            except Exception:  # nosec B110
                pass
            raise
