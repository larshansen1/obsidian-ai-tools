"""Comprehensive tests for YouTube transcript provider integration."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from obsidian_ai_tools.config import Settings
from obsidian_ai_tools.models import VideoMetadata
from obsidian_ai_tools.youtube import YouTubeClient
from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError


@pytest.fixture
def mock_fetched_transcript() -> Any:
    """Mock FetchedTranscript object from youtube-transcript-api v2."""
    # Create mock snippet objects
    snippet1, snippet2, snippet3 = Mock(), Mock(), Mock()
    snippet1.text = (
        "Hello, this is a test video. We're going to demonstrate "
        "the YouTube provider functionality."
    )
    snippet2.text = (
        "This is the second segment with more content to ensure "
        "we have enough characters."
    )
    snippet3.text = "And this is the final part with additional text to pass validation."

    # Create mock FetchedTranscript
    transcript = Mock()
    transcript.snippets = [snippet1, snippet2, snippet3]
    transcript.language_code = "en"

    return transcript


class TestYouTubeClientProviderFallback:
    """Test YouTube client provider fallback chain."""

    @pytest.fixture
    def mock_settings(self, tmp_path: Path) -> Settings:
        """Create test settings with all providers configured."""
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        return Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault_path,
            cache_dir=tmp_path / "cache",
            youtube_api_key="test-yt-key",
            decodo_api_key="test-decodo-key",
            supadata_key="test-supadata-key",
            youtube_transcript_provider_order="direct,supadata,decodo",
        )

    def test_direct_provider_success(
        self, mock_settings: Settings, mock_fetched_transcript: Any
    ) -> None:
        """Test successful transcript fetch from direct provider."""
        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_api_class:
            # Mock the API instance and its fetch method
            mock_api_instance = Mock()
            mock_api_instance.fetch.return_value = mock_fetched_transcript
            mock_api_class.return_value = mock_api_instance

            client = YouTubeClient(mock_settings)
            url = "https://www.youtube.com/watch?v=test123"

            # Mock metadata provider
            with patch.object(
                client.metadata_provider,
                "fetch_metadata",
                return_value={"title": "Test Video", "channel_name": "Test Channel"},
            ):
                result = client.get_video_metadata(url)

            assert isinstance(result, VideoMetadata)
            assert result.video_id == "test123"
            assert result.provider_used == "direct"
            assert "Hello, this is a test video" in result.transcript

    def test_fallback_to_supadata_on_direct_failure(self, mock_settings: Settings) -> None:
        """Test fallback to Supadata when direct provider fails."""
        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_api_class:
            # Mock direct provider failure
            mock_api_instance = Mock()
            mock_api_instance.fetch.side_effect = Exception("Direct fetch failed")
            mock_api_class.return_value = mock_api_instance

            # Mock Supadata success - patch where it's used, not where it's defined
            with patch(
                "obsidian_ai_tools.youtube.SupadataTranscriptProvider"
            ) as mock_supadata_class:
                mock_supadata_instance = Mock()
                # Return tuple (transcript, language) - varied content to pass quality checks
                mock_supadata_instance.fetch_transcript.return_value = (
                    "Welcome to this video about Test Channel 2 content. Today we will "
                    "explore various interesting topics related to software development. "
                    "First, we discuss the importance of proper mocking in unit tests. "
                    "Then we cover best practices for API integration testing strategies. "
                    "Finally, we wrap up with some practical examples and demonstrations.",
                    "en",
                )
                mock_supadata_class.return_value = mock_supadata_instance

                client = YouTubeClient(mock_settings)
                url = "https://www.youtube.com/watch?v=test456"

                with patch.object(
                    client.metadata_provider,
                    "fetch_metadata",
                    return_value={"title": "Test Video 2", "channel_name": "Test Channel 2"},
                ):
                    result = client.get_video_metadata(url)

                assert result.provider_used == "supadata"
                assert "software development" in result.transcript

    def test_all_providers_fail_raises_error(self, mock_settings: Settings) -> None:
        """Test that TranscriptUnavailableError is raised when all providers fail."""
        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_api_class:
            mock_api_instance = Mock()
            mock_api_instance.fetch.side_effect = Exception("Direct failed")
            mock_api_class.return_value = mock_api_instance

            # Patch where classes are used, not where they're defined
            with patch(
                "obsidian_ai_tools.youtube.SupadataTranscriptProvider"
            ) as mock_supadata_class:
                mock_supadata_instance = Mock()
                mock_supadata_instance.fetch_transcript.side_effect = TranscriptUnavailableError(
                    "Supadata failed"
                )
                mock_supadata_class.return_value = mock_supadata_instance

                with patch(
                    "obsidian_ai_tools.youtube.DecodoTranscriptProvider"
                ) as mock_decodo_class:
                    mock_decodo_instance = Mock()
                    mock_decodo_instance.fetch_transcript.side_effect = (
                        TranscriptUnavailableError("Decodo failed")
                    )

                    mock_decodo_class.return_value = mock_decodo_instance

                    client = YouTubeClient(mock_settings)
                    url = "https://www.youtube.com/watch?v=fail123"

                    with pytest.raises(TranscriptUnavailableError, match="All providers failed"):
                        client.get_video_metadata(url)


class TestYouTubeClientCircuitBreaker:
    """Test circuit breaker integration with YouTube client."""

    @pytest.fixture
    def minimal_settings(self, tmp_path: Path) -> Settings:
        """Settings with only direct provider (no paid APIs)."""
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        return Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault_path,
            cache_dir=tmp_path / "cache",
            circuit_breaker_threshold=3,
        )

    def test_circuit_breaker_opens_after_threshold(self, minimal_settings: Settings) -> None:
        """Test that circuit breaker opens after failure threshold."""
        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_api_class:
            mock_api_instance = Mock()
            mock_api_instance.fetch.side_effect = Exception("Always fails")
            mock_api_class.return_value = mock_api_instance

            client = YouTubeClient(minimal_settings)

            # Cause 3 failures (threshold)
            for i in range(3):
                try:
                    client._try_direct_provider(f"fail{i}")
                except TranscriptUnavailableError:
                    pass

            # Circuit breaker should now be open
            assert client.circuit_breaker.is_open()

            # Further attempts should fail immediately
            with pytest.raises(TranscriptUnavailableError, match="circuit breaker open"):
                client._try_direct_provider("test_after_open")


class TestYouTubeClientCaching:
    """Test caching behavior of YouTube client."""

    @pytest.fixture
    def cache_settings(self, tmp_path: Path) -> Settings:
        """Settings for cache testing."""
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        return Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault_path,
            cache_dir=tmp_path / "cache",
            cache_ttl_hours=24,
        )

    def test_cache_hit_skips_providers(
        self, cache_settings: Settings, mock_fetched_transcript: Any
    ) -> None:
        """Test that cache hit skips provider calls."""
        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_api_class:
            mock_api_instance = Mock()
            mock_api_instance.fetch.return_value = mock_fetched_transcript
            mock_api_class.return_value = mock_api_instance

            client = YouTubeClient(cache_settings)
            url = "https://www.youtube.com/watch?v=cached123"

            with patch.object(
                client,
                "_fetch_metadata",
                return_value={"title": "Cached Video", "channel_name": "Test Channel"},
            ):
                # First call populates cache
                result1 = client.get_video_metadata(url)

            # Second call should hit cache (mock should not be called again)
            mock_api_instance.fetch.reset_mock()
            result2 = client.get_video_metadata(url)

            # Should not have called provider again
            mock_api_instance.fetch.assert_not_called()

            # Results should be identical
            assert result1.video_id == result2.video_id
            assert result1.transcript == result2.transcript
