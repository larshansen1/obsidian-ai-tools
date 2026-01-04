"""YouTube content provider."""

from typing import Any

from ..models import VideoMetadata
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
        return client.get_video_metadata(source, provider_order=kwargs.get("provider_order"))
