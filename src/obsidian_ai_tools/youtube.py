"""YouTube transcript fetching with resilient multi-provider architecture."""

import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cache import VideoCache
from .circuit_breaker import CircuitBreaker
from .config import Settings, get_settings
from .models import VideoMetadata
from .youtube_exceptions import (
    InvalidYouTubeURLError,
    TranscriptUnavailableError,
)
from .youtube_providers import (
    DecodoTranscriptProvider,
    SupadataTranscriptProvider,
    UnofficialTranscriptProvider,
    YouTubeDataAPIMetadataProvider,
)

logger = logging.getLogger(__name__)


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from various URL formats.

    Supports:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID
    - https://m.youtube.com/watch?v=VIDEO_ID

    Args:
        url: YouTube URL

    Returns:
        Video ID string

    Raises:
        InvalidYouTubeURLError: If URL format is not recognized
    """
    parsed = urlparse(url)

    # Extract from query parameters (youtube.com/watch?v=...)
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        query_params = parse_qs(parsed.query)
        video_id = query_params.get("v", [None])[0]
        if video_id:
            return video_id

    # Extract from shortened URLs (youtu.be/...)
    if parsed.hostname == "youtu.be":
        video_id = parsed.path.lstrip("/")
        if video_id:
            return video_id

    raise InvalidYouTubeURLError(f"Could not extract video ID from URL: {url}")


class YouTubeClient:
    """Resilient YouTube client with caching and multi-provider fallback."""

    def __init__(self, settings: Settings | None = None):
        """Initialize YouTube client.

        Args:
            settings: Application settings (uses default if None)
        """
        self.settings = settings or get_settings()

        # Initialize cache
        cache_dir = Path(self.settings.cache_dir)
        self.cache = VideoCache(
            cache_dir=cache_dir,
            ttl_hours=self.settings.cache_ttl_hours,
        )

        # Initialize circuit breaker for unofficial API
        circuit_breaker_file = cache_dir / "circuit_breaker_state.json"
        self.circuit_breaker = CircuitBreaker(
            state_file=circuit_breaker_file,
            failure_threshold=self.settings.circuit_breaker_threshold,
            timeout_hours=self.settings.circuit_breaker_timeout_hours,
        )

        # Initialize transcript providers
        self.unofficial_provider = UnofficialTranscriptProvider()

        if self.settings.decodo_api_key:
            self.decodo_provider: DecodoTranscriptProvider | None = DecodoTranscriptProvider(
                self.settings.decodo_api_key
            )
        else:
            self.decodo_provider = None
            logger.warning("Decodo API key not configured - Decodo fallback unavailable")

        if self.settings.supadata_key:
            self.supadata_provider: SupadataTranscriptProvider | None = SupadataTranscriptProvider(
                self.settings.supadata_key
            )
            logger.info("Supadata provider configured as primary transcript source")
        else:
            self.supadata_provider = None
            logger.warning("Supadata API key not configured")

        if self.settings.youtube_api_key:
            self.metadata_provider: YouTubeDataAPIMetadataProvider | None = (
                YouTubeDataAPIMetadataProvider(self.settings.youtube_api_key)
            )
        else:
            self.metadata_provider = None
            logger.warning("YouTube API key not configured - using fallback metadata")

    def _fetch_transcript_with_fallback(
        self, video_id: str, provider_order: str | None = None
    ) -> tuple[str, str, str]:
        """Fetch transcript with provider fallback.

        Provider order is configurable via settings or override parameter.
        Defaults: direct (free) -> supadata (paid) -> decodo (paid)

        Args:
            video_id: YouTube video ID
            provider_order: Optional comma-separated provider order override

        Returns:
            Tuple of (transcript, language_code, provider_used)

        Raises:
            TranscriptUnavailableError: If all providers fail
        """
        errors = []

        # Determine provider order
        order = provider_order or self.settings.youtube_transcript_provider_order
        providers_to_try = [p.strip() for p in order.split(",")]

        logger.info(f"Fetching transcript for {video_id} with provider order: {providers_to_try}")

        # Map provider names to methods
        provider_map = {
            "direct": self._try_direct_provider,
            "supadata": self._try_supadata_provider,
            "decodo": self._try_decodo_provider,
        }

        # Try each provider in order
        for provider_name in providers_to_try:
            if provider_name not in provider_map:
                logger.warning(f"Unknown provider '{provider_name}', skipping")
                continue

            logger.debug(f"Attempting {provider_name} transcript fetch for {video_id}")

            try:
                transcript, lang = provider_map[provider_name](video_id)
                logger.info(
                    f"✓ Successfully fetched transcript from {provider_name} for {video_id}"
                )
                return transcript, lang, provider_name
            except TranscriptUnavailableError as e:
                logger.warning(f"✗ {provider_name.capitalize()} failed for {video_id}: {e}")
                errors.append(f"{provider_name}: {e}")

        # All providers failed
        raise TranscriptUnavailableError(
            f"All providers failed for {video_id}: {'; '.join(errors)}"
        )

    def _try_direct_provider(self, video_id: str) -> tuple[str, str]:
        """Try direct scraping provider (youtube-transcript-api).

        Uses circuit breaker to avoid hammering if repeatedly failing.

        Args:
            video_id: YouTube video ID

        Returns:
            Tuple of (transcript, language_code)

        Raises:
            TranscriptUnavailableError: If provider fails or circuit breaker is open
        """
        if self.circuit_breaker.is_open():
            logger.debug(f"Circuit breaker OPEN - skipping direct provider for {video_id}")
            raise TranscriptUnavailableError("circuit breaker open")

        try:
            transcript, lang = self.unofficial_provider.fetch_transcript(video_id)
            self.circuit_breaker.record_success()
            return transcript, lang
        except TranscriptUnavailableError:
            self.circuit_breaker.record_failure()
            raise

    def _try_supadata_provider(self, video_id: str) -> tuple[str, str]:
        """Try Supadata provider.

        Args:
            video_id: YouTube video ID

        Returns:
            Tuple of (transcript, language_code)

        Raises:
            TranscriptUnavailableError: If provider not configured or fails
        """
        if not self.supadata_provider:
            raise TranscriptUnavailableError("Supadata provider not configured (missing API key)")

        return self.supadata_provider.fetch_transcript(video_id)

    def _try_decodo_provider(self, video_id: str) -> tuple[str, str]:
        """Try Decodo provider.

        Args:
            video_id: YouTube video ID

        Returns:
            Tuple of (transcript, language_code)

        Raises:
            TranscriptUnavailableError: If provider not configured or fails
        """
        if not self.decodo_provider:
            raise TranscriptUnavailableError("Decodo provider not configured (missing API key)")

        return self.decodo_provider.fetch_transcript(video_id)

    def _fetch_metadata(self, video_id: str) -> dict[str, str]:
        """Fetch video metadata.

        Args:
            video_id: YouTube video ID

        Returns:
            Dictionary with title and channel_name
        """
        if self.metadata_provider:
            try:
                logger.debug(f"Fetching metadata from YouTube API for {video_id}")
                metadata = self.metadata_provider.fetch_metadata(video_id)
                logger.info(f"Successfully fetched metadata from YouTube API for {video_id}")
                return metadata
            except Exception as e:
                logger.warning(f"YouTube API metadata fetch failed for {video_id}: {e}")

        # Fallback to placeholder
        logger.warning(f"Using placeholder metadata for {video_id}")
        return {
            "title": f"Video {video_id}",
            "channel_name": "Unknown Channel",
        }

    def get_video_metadata(self, url: str, provider_order: str | None = None) -> VideoMetadata:
        """Fetch complete video metadata and transcript.

        This is the main entry point with caching and multi-provider fallback.

        Args:
            url: YouTube video URL
            provider_order: Optional comma-separated provider order override

        Returns:
            VideoMetadata with all fields populated

        Raises:
            InvalidYouTubeURLError: If URL is invalid
            TranscriptUnavailableError: If transcript cannot be fetched or is low quality
        """
        # Extract video ID
        video_id = extract_video_id(url)

        # Check cache first
        cached = self.cache.get(video_id)
        if cached:
            logger.info(f"Cache HIT for {video_id}")
            return cached

        logger.info(f"Cache MISS for {video_id} - fetching from providers")

        # Fetch metadata
        metadata_dict = self._fetch_metadata(video_id)

        # Fetch transcript with fallback
        transcript, language, provider = self._fetch_transcript_with_fallback(
            video_id, provider_order
        )

        # Validate transcript quality before creating note
        from .transcript_validation import validate_transcript_quality, check_transcript_relevance

        quality_issue = validate_transcript_quality(transcript, metadata_dict["title"])
        if quality_issue:
            logger.warning(f"Transcript quality check failed for {video_id}: {quality_issue}")
            raise TranscriptUnavailableError(
                f"Transcript quality too low for {video_id}: {quality_issue}"
            )

        # Check transcript relevance to title
        if not check_transcript_relevance(transcript, metadata_dict["title"]):
            logger.warning(
                f"Transcript appears irrelevant to video title for {video_id}. "
                f"Title: {metadata_dict['title']}"
            )
            raise TranscriptUnavailableError(
                f"Transcript content does not match video title for {video_id}. "
                "This may indicate corrupted or mismatched transcript data."
            )

        # Build result
        result = VideoMetadata(
            video_id=video_id,
            title=metadata_dict["title"],
            channel_name=metadata_dict["channel_name"],
            url=url,
            transcript=transcript,
            source_language=language,
            provider_used=provider,
        )

        # Cache the result
        self.cache.set(video_id, result, provider)
        logger.info(f"Cached result for {video_id} (provider: {provider})")

        return result


# Convenience function for backward compatibility
def get_video_metadata(url: str) -> VideoMetadata:
    """Fetch video metadata using default client.

    Args:
        url: YouTube video URL

    Returns:
        VideoMetadata

    Raises:
        InvalidYouTubeURLError: If URL is invalid
        TranscriptUnavailableError: If transcript cannot be fetched
    """
    client = YouTubeClient()
    return client.get_video_metadata(url)
