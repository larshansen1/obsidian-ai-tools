"""YouTube transcript and metadata providers."""

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from .youtube_exceptions import (
    TranscriptUnavailableError,
)

logger = logging.getLogger(__name__)


class TranscriptProvider(ABC):
    """Abstract base class for transcript providers."""

    @abstractmethod
    def fetch_transcript(self, video_id: str) -> tuple[str, str]:
        """Fetch transcript for a video.

        Args:
            video_id: YouTube video ID

        Returns:
            Tuple of (transcript_text, language_code)

        Raises:
            TranscriptUnavailableError: If transcript cannot be fetched
        """
        pass


class MetadataProvider(ABC):
    """Abstract base class for metadata providers."""

    @abstractmethod
    def fetch_metadata(self, video_id: str) -> dict[str, str]:
        """Fetch video metadata.

        Args:
            video_id: YouTube video ID

        Returns:
            Dictionary with title, channel_name, and other metadata

        Raises:
            Exception: If metadata cannot be fetched
        """
        pass


class UnofficialTranscriptProvider(TranscriptProvider):
    """Transcript provider using youtube-transcript-api (unofficial)."""

    def __init__(self) -> None:
        """Initialize provider."""
        self.proxies = None

    def fetch_transcript(self, video_id: str) -> tuple[str, str]:
        """Fetch transcript using unofficial API.

        Note: proxy support was removed in youtube-transcript-api v1.2+.
        We use the new v2 API which returns FetchedTranscript objects.
        """
        try:
            # v2 API: create instance and call fetch()
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id, languages=["en"])

            # Get transcript text from the FetchedTranscript object
            # The object has a .snippets property with the text segments
            full_transcript = " ".join(snippet.text for snippet in transcript.snippets)

            # Get the language from the transcript
            language_code = transcript.language_code

            return full_transcript.strip(), language_code

        except (NoTranscriptFound, TranscriptsDisabled) as e:
            raise TranscriptUnavailableError(f"No transcript available for video {video_id}") from e
        except VideoUnavailable as e:
            raise TranscriptUnavailableError(f"Video {video_id} is unavailable or private") from e
        except Exception as e:
            raise TranscriptUnavailableError(
                f"Failed to fetch transcript for {video_id}: {e}"
            ) from e


class DecodoTranscriptProvider(TranscriptProvider):
    """Transcript provider using Decodo Scraper API."""

    def __init__(self, api_key: str):
        """Initialize with Decodo API key."""
        self.api_key = api_key
        self.base_url = "https://scraper-api.decodo.com/v2/scrape"

    def fetch_transcript(self, video_id: str) -> tuple[str, str]:
        """Fetch transcript using Decodo Scraper API (via subtitles)."""
        try:
            response = httpx.post(
                self.base_url,
                json={
                    "target": "youtube_subtitles",  # Use subtitles instead of transcript
                    "query": video_id,
                    "language_code": "en",
                },
                headers={
                    "Authorization": f"Basic {self.api_key}",  # Decodo uses Basic auth
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            response.raise_for_status()

            data = response.json()

            # Decodo returns nested structure: {results: {data: {subtitles: {events: [...]}}}}
            if isinstance(data, dict) and "results" in data:
                results = data["results"]

                # Navigate to subtitle events
                if isinstance(results, dict):
                    subtitles_data = results.get("data", {}).get("subtitles", {})
                    events = subtitles_data.get("events", [])

                    # Extract text from all segments in all events
                    text_segments = []
                    for event in events:
                        segs = event.get("segs", [])
                        for seg in segs:
                            utf8_text = seg.get("utf8", "").strip()
                            if utf8_text and utf8_text != "\n":
                                text_segments.append(utf8_text)

                    if text_segments:
                        full_transcript = " ".join(text_segments)
                        return full_transcript.strip(), "en"

            raise TranscriptUnavailableError(
                f"No transcript content found in Decodo response for {video_id}"
            )

        except httpx.HTTPStatusError as e:
            raise TranscriptUnavailableError(
                f"Decodo API error for {video_id}: {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise TranscriptUnavailableError(
                f"Decodo API request failed for {video_id}: {e}"
            ) from e
        except Exception as e:
            raise TranscriptUnavailableError(
                f"Failed to fetch transcript from Decodo for {video_id}: {e}"
            ) from e


class SupadataTranscriptProvider(TranscriptProvider):
    """Transcript provider using Supadata API.

    Uses the Supadata unified API to fetch YouTube transcripts.
    Supports language preference (defaults to English).
    """

    def __init__(self, api_key: str, lang: str = "en"):
        """Initialize with Supadata API key.

        Args:
            api_key: Supadata API key
            lang: Preferred transcript language (default: "en")
        """
        self.api_key = api_key
        self.lang = lang
        self._client = None

    def _get_client(self) -> Any:
        """Lazy initialization of Supadata client."""
        if self._client is None:
            import supadata

            self._client = supadata.Supadata(api_key=self.api_key)
        return self._client

    def fetch_transcript(self, video_id: str) -> tuple[str, str]:
        """Fetch transcript using Supadata unified API.

        Args:
            video_id: YouTube video ID

        Returns:
            Tuple of (transcript_text, language_code)

        Raises:
            TranscriptUnavailableError: If transcript cannot be fetched
        """
        try:
            client = self._get_client()
            url = f"https://www.youtube.com/watch?v={video_id}"

            result = client.transcript(url=url, lang=self.lang, text=True)

            # Handle BatchJob objects (indicates async processing or unavailable content)
            if hasattr(result, '__class__') and result.__class__.__name__ == 'BatchJob':
                raise TranscriptUnavailableError(
                    f"Transcript not immediately available from Supadata for {video_id} "
                    "(returned BatchJob - may require async processing or video has no transcript)"
                )

            # Check for content attribute before accessing
            if not hasattr(result, 'content'):
                raise TranscriptUnavailableError(
                    f"Unexpected response from Supadata for {video_id}: "
                    f"result type {type(result).__name__} has no 'content' attribute"
                )

            if not result.content:
                raise TranscriptUnavailableError(f"Empty transcript from Supadata for {video_id}")

            # Get language, with fallback
            language = getattr(result, 'lang', None) or self.lang

            return result.content, language

        except ImportError as e:
            raise TranscriptUnavailableError(f"Supadata library not installed: {e}") from e
        except AttributeError as e:
            # Catch attribute errors when accessing undefined attributes
            raise TranscriptUnavailableError(
                f"Failed to parse Supadata response for {video_id}: {e}"
            ) from e
        except TranscriptUnavailableError:
            # Re-raise our own errors
            raise
        except Exception as e:
            raise TranscriptUnavailableError(
                f"Failed to fetch transcript from Supadata for {video_id}: {e}"
            ) from e


class YouTubeDataAPIMetadataProvider(MetadataProvider):
    """Metadata provider using official YouTube Data API v3."""

    def __init__(self, api_key: str):
        """Initialize with YouTube API key."""
        self.api_key = api_key
        # Note: We'll import googleapiclient lazily to avoid issues if not installed
        self._youtube = None

    def _get_client(self) -> Any:
        """Lazy initialization of YouTube API client."""
        if self._youtube is None:
            from googleapiclient.discovery import build

            self._youtube = build("youtube", "v3", developerKey=self.api_key)
        return self._youtube

    def fetch_metadata(self, video_id: str) -> dict[str, str]:
        """Fetch metadata using official YouTube Data API v3."""
        try:
            youtube = self._get_client()

            response = youtube.videos().list(part="snippet", id=video_id).execute()

            if not response.get("items"):
                raise ValueError(f"Video {video_id} not found")

            snippet = response["items"][0]["snippet"]

            return {
                "title": snippet["title"],
                "channel_name": snippet["channelTitle"],
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
            }

        except Exception as e:
            raise Exception(f"Failed to fetch metadata from YouTube API: {e}") from e
