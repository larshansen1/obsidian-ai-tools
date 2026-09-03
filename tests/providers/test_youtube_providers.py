"""Comprehensive tests for YouTube transcript provider integration."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from obsidian_ai_tools.config import Settings
from obsidian_ai_tools.models import VideoMetadata
from obsidian_ai_tools.youtube import YouTubeClient
from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError
from obsidian_ai_tools.youtube_providers import (
    DecodoTranscriptProvider,
    SupadataTranscriptProvider,
    UnofficialTranscriptProvider,
    YouTubeDataAPIMetadataProvider,
)


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
        "This is the second segment with more content to ensure we have enough characters."
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
                    mock_decodo_instance.fetch_transcript.side_effect = TranscriptUnavailableError(
                        "Decodo failed"
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


class TestDecodoTranscriptProvider:
    """Test Decodo response parsing and network failures."""

    def test_fetch_transcript_extracts_nested_segments(self) -> None:
        """Subtitle events should be flattened into transcript text."""
        response = MagicMock()
        response.json.return_value = {
            "results": {
                "data": {
                    "subtitles": {
                        "events": [
                            {"segs": [{"utf8": "Hello "}, {"utf8": "\n"}, {"utf8": "world"}]}
                        ]
                    }
                }
            }
        }

        with patch("obsidian_ai_tools.youtube_providers.httpx.post", return_value=response):
            transcript, language = DecodoTranscriptProvider("key").fetch_transcript("abc")

        assert transcript == "Hello world"
        assert language == "en"

    def test_fetch_transcript_rejects_empty_response(self) -> None:
        """Responses without subtitle segments should produce a provider error."""
        response = MagicMock()
        response.json.return_value = {"results": {"data": {"subtitles": {"events": []}}}}

        with (
            patch("obsidian_ai_tools.youtube_providers.httpx.post", return_value=response),
            pytest.raises(TranscriptUnavailableError, match="Failed to fetch transcript"),
        ):
            DecodoTranscriptProvider("key").fetch_transcript("abc")

    def test_fetch_transcript_reports_http_status(self) -> None:
        """HTTP failures should retain the Decodo status code."""
        request = httpx.Request("POST", "https://decodo.example")
        error_response = httpx.Response(429, request=request)
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "rate limited", request=request, response=error_response
        )

        with (
            patch("obsidian_ai_tools.youtube_providers.httpx.post", return_value=response),
            pytest.raises(TranscriptUnavailableError, match="Decodo API error.*429"),
        ):
            DecodoTranscriptProvider("key").fetch_transcript("abc")

    def test_fetch_transcript_reports_request_error(self) -> None:
        """Network failures should use the Decodo request error path."""
        request = httpx.Request("POST", "https://decodo.example")

        with (
            patch(
                "obsidian_ai_tools.youtube_providers.httpx.post",
                side_effect=httpx.RequestError("offline", request=request),
            ),
            pytest.raises(TranscriptUnavailableError, match="request failed"),
        ):
            DecodoTranscriptProvider("key").fetch_transcript("abc")


class TestSupadataTranscriptProvider:
    """Test Supadata result handling."""

    def test_fetch_transcript_uses_configured_language_as_fallback(self) -> None:
        """Transcript results without lang should use the configured language."""
        provider = SupadataTranscriptProvider("key", lang="da")
        client: Any = MagicMock()
        client.transcript.return_value = MagicMock(content="Hej", lang=None)
        provider._client = client

        transcript, language = provider.fetch_transcript("abc")

        assert transcript == "Hej"
        assert language == "da"

    @pytest.mark.parametrize(
        ("result", "message"),
        [
            (type("BatchJob", (), {})(), "not immediately available"),
            (object(), "has no 'content' attribute"),
            (MagicMock(content=""), "Empty transcript"),
        ],
    )
    def test_fetch_transcript_rejects_unusable_results(self, result: Any, message: str) -> None:
        """Async, malformed, and empty responses should be rejected."""
        provider = SupadataTranscriptProvider("key")
        client: Any = MagicMock()
        client.transcript.return_value = result
        provider._client = client

        with pytest.raises(TranscriptUnavailableError, match=message):
            provider.fetch_transcript("abc")


class TestYouTubeDataAPIMetadataProvider:
    """Test official YouTube metadata response mapping."""

    def test_fetch_metadata_maps_snippet(self) -> None:
        """Snippet fields should map to normalized metadata."""
        provider = YouTubeDataAPIMetadataProvider("key")
        execute = MagicMock(
            return_value={
                "items": [
                    {
                        "snippet": {
                            "title": "Video",
                            "channelTitle": "Channel",
                        }
                    }
                ]
            }
        )
        youtube_client: Any = MagicMock()
        youtube_client.videos.return_value.list.return_value.execute = execute
        provider._youtube = youtube_client

        assert provider.fetch_metadata("abc") == {
            "title": "Video",
            "channel_name": "Channel",
            "description": "",
            "published_at": "",
        }

    def test_fetch_metadata_rejects_missing_video(self) -> None:
        """Missing API items should be reported as metadata failures."""
        provider = YouTubeDataAPIMetadataProvider("key")
        youtube_client: Any = MagicMock()
        youtube_client.videos.return_value.list.return_value.execute.return_value = {"items": []}
        provider._youtube = youtube_client

        with pytest.raises(Exception, match="Video abc not found"):
            provider.fetch_metadata("abc")


class TestUnofficialTranscriptProviderExact:
    """Exact request arguments and messages for the unofficial provider."""

    def test_initializes_proxies_to_none(self) -> None:
        assert UnofficialTranscriptProvider().proxies is None

    def test_fetch_transcript_exact_request_and_join(self) -> None:
        """The fetch call and snippet join must be exact."""
        provider = UnofficialTranscriptProvider()
        fetched = Mock(language_code="en")
        fetched.snippets = [Mock(text="Hello"), Mock(text="world")]

        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_api:
            mock_api.return_value.fetch.return_value = fetched
            transcript, language = provider.fetch_transcript("vid123")

        mock_api.assert_called_once_with()
        mock_api.return_value.fetch.assert_called_once_with("vid123", languages=["en"])
        assert transcript == "Hello world"
        assert language == "en"

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (NoTranscriptFound("vid9", ["en"], Mock()), "No transcript available for video vid9"),
            (TranscriptsDisabled("vid9"), "No transcript available for video vid9"),
            (VideoUnavailable("vid9"), "Video vid9 is unavailable or private"),
            (RuntimeError("boom"), "Failed to fetch transcript for vid9: boom"),
        ],
    )
    def test_fetch_transcript_error_messages(self, error: Exception, expected: str) -> None:
        """Each failure type surfaces its dedicated exact message."""
        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_api:
            mock_api.return_value.fetch.side_effect = error

            with pytest.raises(TranscriptUnavailableError) as exc:
                UnofficialTranscriptProvider().fetch_transcript("vid9")

        assert str(exc.value) == expected


class TestDecodoTranscriptProviderExactCalls:
    """Exact request construction and messages for the Decodo provider."""

    @staticmethod
    def make_response(payload: dict) -> MagicMock:
        response = MagicMock()
        response.json.return_value = payload
        return response

    def test_fetch_transcript_posts_exact_request(self) -> None:
        """The POST target, JSON body, auth header, and timeout are exact."""
        response = self.make_response(
            {
                "results": {
                    "data": {
                        "subtitles": {"events": [{"segs": [{"utf8": "Hello"}, {"utf8": "world"}]}]}
                    }
                }
            }
        )

        with patch(
            "obsidian_ai_tools.youtube_providers.httpx.post", return_value=response
        ) as mock_post:
            transcript, language = DecodoTranscriptProvider("secret-key").fetch_transcript("vid123")

        mock_post.assert_called_once_with(
            "https://scraper-api.decodo.com/v2/scrape",
            json={"target": "youtube_subtitles", "query": "vid123", "language_code": "en"},
            headers={
                "Authorization": "Basic secret-key",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        assert transcript == "Hello world"
        assert language == "en"

    @pytest.mark.parametrize(
        "payload",
        [
            {"results": {"data": {"subtitles": {"events": []}}}},  # empty events
            {"results": {"data": {"subtitles": {"events": [{}]}}}},  # no segs key
            {"results": {"data": {"subtitles": {"events": [{"segs": [{}]}]}}}},  # no utf8
            {"results": {"data": {"subtitles": {"events": [{"segs": [{"utf8": "\n"}]}]}}}},
            {"results": {"data": {"subtitles": {}}}},  # no events key
            {"results": {}},  # no data/subtitles keys at all
            {},  # no results key at all
        ],
    )
    def test_fetch_transcript_no_content_exact_message(self, payload: dict) -> None:
        """Contentless responses all produce the same exact no-content error."""
        with patch(
            "obsidian_ai_tools.youtube_providers.httpx.post",
            return_value=self.make_response(payload),
        ):
            with pytest.raises(TranscriptUnavailableError) as exc:
                DecodoTranscriptProvider("key").fetch_transcript("vidX")

        assert str(exc.value) == (
            "Failed to fetch transcript from Decodo for vidX: "
            "No transcript content found in Decodo response for vidX"
        )

    def test_fetch_transcript_http_status_exact_message(self) -> None:
        request = httpx.Request("POST", "https://decodo.example")
        error_response = httpx.Response(429, request=request)
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "rate limited", request=request, response=error_response
        )

        with patch("obsidian_ai_tools.youtube_providers.httpx.post", return_value=response):
            with pytest.raises(TranscriptUnavailableError) as exc:
                DecodoTranscriptProvider("key").fetch_transcript("abc")

        assert str(exc.value) == "Decodo API error for abc: 429"

    def test_fetch_transcript_request_error_exact_message(self) -> None:
        request = httpx.Request("POST", "https://decodo.example")

        with patch(
            "obsidian_ai_tools.youtube_providers.httpx.post",
            side_effect=httpx.RequestError("offline", request=request),
        ):
            with pytest.raises(TranscriptUnavailableError) as exc:
                DecodoTranscriptProvider("key").fetch_transcript("abc")

        assert str(exc.value) == "Decodo API request failed for abc: offline"


class TestSupadataTranscriptProviderExactCalls:
    """Exact request arguments and messages for the Supadata provider."""

    def test_fetch_transcript_passes_exact_args(self) -> None:
        provider = SupadataTranscriptProvider("supa-key")
        client: Any = MagicMock()
        client.transcript.return_value = SimpleNamespace(content="text here", lang="es")
        provider._client = client

        transcript, language = provider.fetch_transcript("vid456")

        client.transcript.assert_called_once_with(
            url="https://www.youtube.com/watch?v=vid456", lang="en", text=True
        )
        assert transcript == "text here"
        assert language == "es"

    def test_fetch_transcript_honors_configured_language(self) -> None:
        provider = SupadataTranscriptProvider("supa-key", lang="ja")
        client: Any = MagicMock()
        client.transcript.return_value = MagicMock(content="hai", lang="ja")
        provider._client = client

        transcript, language = provider.fetch_transcript("vid")

        client.transcript.assert_called_once_with(
            url="https://www.youtube.com/watch?v=vid", lang="ja", text=True
        )
        assert transcript == "hai"
        assert language == "ja"

    def test_fetch_transcript_defaults_language_without_lang_attr(self) -> None:
        provider = SupadataTranscriptProvider("supa-key")
        client: Any = MagicMock()
        client.transcript.return_value = SimpleNamespace(content="hej hej")
        provider._client = client

        transcript, language = provider.fetch_transcript("vid")

        assert transcript == "hej hej"
        assert language == "en"

    def test_fetch_transcript_batch_job_exact_message(self) -> None:
        provider = SupadataTranscriptProvider("key")
        client: Any = MagicMock()
        client.transcript.return_value = type("BatchJob", (), {})()
        provider._client = client

        with pytest.raises(TranscriptUnavailableError) as exc:
            provider.fetch_transcript("vid")

        assert str(exc.value) == (
            "Transcript not immediately available from Supadata for vid "
            "(returned BatchJob - may require async processing or video has no transcript)"
        )

    def test_fetch_transcript_missing_content_exact_message(self) -> None:
        provider = SupadataTranscriptProvider("key")
        client: Any = MagicMock()
        client.transcript.return_value = type("OtherThing", (), {})()
        provider._client = client

        with pytest.raises(TranscriptUnavailableError) as exc:
            provider.fetch_transcript("vid")

        assert str(exc.value) == (
            "Unexpected response from Supadata for vid: "
            "result type OtherThing has no 'content' attribute"
        )

    def test_fetch_transcript_parse_error_message(self) -> None:
        provider = SupadataTranscriptProvider("key")
        client: Any = MagicMock()
        client.transcript.side_effect = AttributeError("boom")
        provider._client = client

        with pytest.raises(TranscriptUnavailableError) as exc:
            provider.fetch_transcript("vid")

        assert str(exc.value) == "Failed to parse Supadata response for vid: boom"

    def test_fetch_transcript_generic_error_message(self) -> None:
        provider = SupadataTranscriptProvider("key")
        client: Any = MagicMock()
        client.transcript.side_effect = ValueError("kaput")
        provider._client = client

        with pytest.raises(TranscriptUnavailableError) as exc:
            provider.fetch_transcript("vid")

        assert str(exc.value) == "Failed to fetch transcript from Supadata for vid: kaput"

    def test_fetch_transcript_import_error_message(self) -> None:
        provider = SupadataTranscriptProvider("key")

        with patch.dict("sys.modules", {"supadata": None}):
            with pytest.raises(TranscriptUnavailableError, match="Supadata library not installed"):
                provider.fetch_transcript("vid")


class TestSupadataClientLazyInit:
    """Lazy construction and reuse of the Supadata client."""

    def test_get_client_builds_supadata_once(self) -> None:
        provider = SupadataTranscriptProvider("supa-key")
        assert provider._client is None
        fake_module = MagicMock()
        fake_module.Supadata.return_value = "supadata-client"

        with patch.dict("sys.modules", {"supadata": fake_module}):
            first = provider._get_client()
            second = provider._get_client()

        fake_module.Supadata.assert_called_once_with(api_key="supa-key")
        assert first == "supadata-client"
        assert first is second


class TestMetadataProviderLazyInit:
    """Lazy construction and reuse of the YouTube Data API client."""

    def test_get_client_builds_api_once(self) -> None:
        provider = YouTubeDataAPIMetadataProvider("yt-key")
        assert provider._youtube is None

        with patch("googleapiclient.discovery.build") as mock_build:
            mock_build.return_value = "youtube-client"
            first = provider._get_client()
            second = provider._get_client()

        mock_build.assert_called_once_with("youtube", "v3", developerKey="yt-key")
        assert first == "youtube-client"
        assert first is second


class TestMetadataProviderExactCalls:
    """Exact request arguments and error wrapping for metadata fetching."""

    def test_fetch_metadata_passes_exact_list_args(self) -> None:
        provider = YouTubeDataAPIMetadataProvider("key")
        youtube_client: Any = MagicMock()
        youtube_client.videos.return_value.list.return_value.execute.return_value = {
            "items": [{"snippet": {"title": "T", "channelTitle": "C"}}]
        }
        provider._youtube = youtube_client

        provider.fetch_metadata("vid")

        youtube_client.videos.assert_called_once_with()
        youtube_client.videos.return_value.list.assert_called_once_with(part="snippet", id="vid")

    def test_fetch_metadata_maps_optional_fields(self) -> None:
        provider = YouTubeDataAPIMetadataProvider("key")
        youtube_client: Any = MagicMock()
        youtube_client.videos.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "title": "T",
                        "channelTitle": "C",
                        "description": "Desc text",
                        "publishedAt": "2024-01-01",
                    }
                }
            ]
        }
        provider._youtube = youtube_client

        assert provider.fetch_metadata("vid") == {
            "title": "T",
            "channel_name": "C",
            "description": "Desc text",
            "published_at": "2024-01-01",
        }

    def test_fetch_metadata_wraps_errors_exactly(self) -> None:
        provider = YouTubeDataAPIMetadataProvider("key")
        youtube_client: Any = MagicMock()
        youtube_client.videos.return_value.list.return_value.execute.side_effect = ValueError(
            "Video vid not found"
        )
        provider._youtube = youtube_client

        with pytest.raises(Exception) as exc:
            provider.fetch_metadata("vid")

        assert str(exc.value) == "Failed to fetch metadata from YouTube API: Video vid not found"
