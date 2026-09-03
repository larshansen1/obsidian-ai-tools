"""Tests for YouTube transcript fetching functionality."""

import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from obsidian_ai_tools.config import Settings
from obsidian_ai_tools.models import VideoMetadata
from obsidian_ai_tools.obsidian import sanitize_filename
from obsidian_ai_tools.youtube import (
    InvalidYouTubeURLError,
    YouTubeClient,
    extract_video_id,
)
from obsidian_ai_tools.youtube import (
    get_video_metadata as module_get_video_metadata,
)
from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError


def _assert_logged(caplog: pytest.LogCaptureFixture, message: str) -> None:
    """Assert an exact log message was emitted (kills None/XX/case variants)."""
    messages = [record.getMessage() for record in caplog.records]
    assert message in messages, f"Expected log message {message!r}, got {messages}"


class TestExtractVideoId:
    """Tests for extract_video_id function."""

    def test_extract_from_standard_url(self) -> None:
        """Test extraction from standard youtube.com/watch?v= URL."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_from_short_url(self) -> None:
        """Test extraction from youtu.be shortened URL."""
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_from_mobile_url(self) -> None:
        """Test extraction from mobile m.youtube.com URL."""
        url = "https://m.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_without_www(self) -> None:
        """Test extraction from URL without www."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_invalid_url_raises_error(self) -> None:
        """Test that invalid URL raises InvalidYouTubeURLError."""
        with pytest.raises(InvalidYouTubeURLError):
            extract_video_id("https://example.com/video")

    def test_missing_video_id_raises_error(self) -> None:
        """Test that URL without video ID raises error."""
        with pytest.raises(InvalidYouTubeURLError):
            extract_video_id("https://www.youtube.com/watch")

    def test_invalid_url_error_message_is_exact(self) -> None:
        """The raised message must include the full offending URL."""
        with pytest.raises(InvalidYouTubeURLError, match="https://example.com/video") as exc:
            extract_video_id("https://example.com/video")

        assert str(exc.value) == "Could not extract video ID from URL: https://example.com/video"

    def test_short_url_preserves_leading_characters(self) -> None:
        """Only the leading slash is stripped from the short URL path."""
        assert extract_video_id("https://youtu.be/XXdQw4w9WgXcQ") == "XXdQw4w9WgXcQ"


class TestYouTubeClientConstruction:
    """YouTubeClient cache/circuit-breaker wiring and provider selection."""

    @staticmethod
    def make_settings(tmp_path: Path) -> Settings:
        vault = tmp_path / "vault"
        vault.mkdir()
        return Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault,
            cache_dir=tmp_path / "cache",
            decodo_api_key=None,
            supadata_key=None,
            youtube_api_key=None,
        )

    def test_client_uses_passed_settings_not_defaults(self, tmp_path: Path) -> None:
        """Explicit settings must win over get_settings() defaults."""
        settings = self.make_settings(tmp_path)
        with (
            patch("obsidian_ai_tools.youtube.VideoCache") as mock_cache,
            patch("obsidian_ai_tools.youtube.CircuitBreaker") as mock_breaker,
            patch("obsidian_ai_tools.youtube.UnofficialTranscriptProvider"),
        ):
            YouTubeClient(settings)

        mock_cache.assert_called_once_with(
            cache_dir=Path(settings.cache_dir),
            ttl_hours=settings.cache_ttl_hours,
        )
        mock_breaker.assert_called_once_with(
            state_file=Path(settings.cache_dir) / "circuit_breaker_state.json",
            failure_threshold=settings.circuit_breaker_threshold,
            timeout_hours=settings.circuit_breaker_timeout_hours,
        )

    def test_client_builds_providers_from_keys(self, tmp_path: Path) -> None:
        """Configured API keys must instantiate the matching providers."""
        vault = tmp_path / "vault"
        vault.mkdir()
        settings = Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault,
            cache_dir=tmp_path / "cache",
            decodo_api_key="dec-key",
            supadata_key="supa-key",
            youtube_api_key="yt-key",
        )
        with (
            patch("obsidian_ai_tools.youtube.VideoCache"),
            patch("obsidian_ai_tools.youtube.CircuitBreaker"),
            patch("obsidian_ai_tools.youtube.UnofficialTranscriptProvider"),
            patch("obsidian_ai_tools.youtube.DecodoTranscriptProvider") as mock_decodo,
            patch("obsidian_ai_tools.youtube.SupadataTranscriptProvider") as mock_supa,
            patch("obsidian_ai_tools.youtube.YouTubeDataAPIMetadataProvider") as mock_meta,
        ):
            client = YouTubeClient(settings)

        mock_decodo.assert_called_once_with("dec-key")
        mock_supa.assert_called_once_with("supa-key")
        mock_meta.assert_called_once_with("yt-key")
        assert client.decodo_provider is not None
        assert client.supadata_provider is not None
        assert client.metadata_provider is not None

    def test_client_defaults_providers_to_none_without_keys(self, tmp_path: Path) -> None:
        """Missing keys leave the corresponding provider slots as None."""
        client = YouTubeClient(self.make_settings(tmp_path))

        assert client.decodo_provider is None
        assert client.supadata_provider is None
        assert client.metadata_provider is None

    def test_client_warns_when_keys_missing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Each missing key produces its own warning message."""
        caplog.set_level(logging.WARNING, logger="obsidian_ai_tools.youtube")
        YouTubeClient(self.make_settings(tmp_path))

        _assert_logged(caplog, "Decodo API key not configured - Decodo fallback unavailable")
        _assert_logged(caplog, "Supadata API key not configured")
        _assert_logged(caplog, "YouTube API key not configured - using fallback metadata")

    def test_client_logs_when_supadata_configured(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A configured Supadata key logs the primary-source notice."""
        vault = tmp_path / "vault"
        vault.mkdir()
        settings = Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault,
            cache_dir=tmp_path / "cache",
            supadata_key="supa-key",
        )
        caplog.set_level(logging.INFO, logger="obsidian_ai_tools.youtube")
        YouTubeClient(settings)

        _assert_logged(caplog, "Supadata provider configured as primary transcript source")


class TestFetchTranscriptFallback:
    """Provider ordering and error aggregation in _fetch_transcript_with_fallback."""

    @staticmethod
    def make_client(tmp_path: Path) -> YouTubeClient:
        return YouTubeClient(TestYouTubeClientConstruction.make_settings(tmp_path))

    def test_joins_errors_with_semicolon_separator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        monkeypatch.setattr(
            client.unofficial_provider,
            "fetch_transcript",
            Mock(side_effect=TranscriptUnavailableError("E1")),
        )

        with pytest.raises(
            TranscriptUnavailableError,
            match=r"All providers failed for vid123: direct: E1",
        ):
            client._fetch_transcript_with_fallback("vid123", "direct")

    def test_skips_unknown_provider_and_continues(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        monkeypatch.setattr(
            client.unofficial_provider,
            "fetch_transcript",
            Mock(return_value=("hello transcript words", "en")),
        )

        caplog.set_level(logging.WARNING, logger="obsidian_ai_tools.youtube")
        result = client._fetch_transcript_with_fallback("vid1", "bogus,direct")

        assert result == ("hello transcript words", "en", "direct")
        _assert_logged(caplog, "Unknown provider 'bogus', skipping")

    def test_uses_decodo_provider_when_first_in_order(self, tmp_path: Path) -> None:
        client = self.make_client(tmp_path)
        client.decodo_provider = Mock()
        client.decodo_provider.fetch_transcript.return_value = ("decodo text", "en")

        result = client._fetch_transcript_with_fallback("vid2", "decodo")

        assert result == ("decodo text", "en", "decodo")
        client.decodo_provider.fetch_transcript.assert_called_once_with("vid2")

    def test_supadata_failure_message_is_aggregated(self, tmp_path: Path) -> None:
        client = self.make_client(tmp_path)
        client.supadata_provider = Mock()
        client.supadata_provider.fetch_transcript.side_effect = TranscriptUnavailableError(
            "Supadata boom"
        )

        with pytest.raises(
            TranscriptUnavailableError,
            match=r"All providers failed for vid4: supadata: Supadata boom",
        ):
            client._fetch_transcript_with_fallback("vid4", "supadata")


class TestTryProviderMethods:
    """Individual provider attempt methods."""

    @staticmethod
    def make_client(tmp_path: Path) -> YouTubeClient:
        return YouTubeClient(TestYouTubeClientConstruction.make_settings(tmp_path))

    def test_try_direct_success_passes_video_id_and_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        fetch_transcript = Mock(return_value=("text here", "es"))
        record_success = Mock()
        monkeypatch.setattr(client.unofficial_provider, "fetch_transcript", fetch_transcript)
        monkeypatch.setattr(client.circuit_breaker, "record_success", record_success)

        result = client._try_direct_provider("videoX")

        assert result == ("text here", "es")
        fetch_transcript.assert_called_once_with("videoX")
        record_success.assert_called_once()

    def test_try_direct_failure_records_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        fetch_transcript = Mock(side_effect=TranscriptUnavailableError("no"))
        record_failure = Mock()
        monkeypatch.setattr(client.unofficial_provider, "fetch_transcript", fetch_transcript)
        monkeypatch.setattr(client.circuit_breaker, "record_failure", record_failure)

        with pytest.raises(TranscriptUnavailableError, match="no"):
            client._try_direct_provider("videoX")

        record_failure.assert_called_once()

    def test_try_direct_open_circuit_message(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        monkeypatch.setattr(client.circuit_breaker, "is_open", Mock(return_value=True))

        caplog.set_level(logging.DEBUG, logger="obsidian_ai_tools.youtube")
        with pytest.raises(TranscriptUnavailableError) as exc:
            client._try_direct_provider("videoX")

        assert str(exc.value) == "circuit breaker open"
        _assert_logged(caplog, "Circuit breaker OPEN - skipping direct provider for videoX")

    def test_try_supadata_not_configured_message(self, tmp_path: Path) -> None:
        client = self.make_client(tmp_path)
        assert client.supadata_provider is None

        with pytest.raises(TranscriptUnavailableError) as exc:
            client._try_supadata_provider("vid")

        assert str(exc.value) == "Supadata provider not configured (missing API key)"

    def test_try_decodo_not_configured_message(self, tmp_path: Path) -> None:
        client = self.make_client(tmp_path)
        assert client.decodo_provider is None

        with pytest.raises(TranscriptUnavailableError) as exc:
            client._try_decodo_provider("vid")

        assert str(exc.value) == "Decodo provider not configured (missing API key)"

    def test_try_supadata_forwards_video_id(self, tmp_path: Path) -> None:
        client = self.make_client(tmp_path)
        client.supadata_provider = Mock()
        client.supadata_provider.fetch_transcript.return_value = ("supa words", "fr")

        result = client._try_supadata_provider("otherVid")

        assert result == ("supa words", "fr")
        client.supadata_provider.fetch_transcript.assert_called_once_with("otherVid")

    def test_try_decodo_forwards_video_id(self, tmp_path: Path) -> None:
        client = self.make_client(tmp_path)
        client.decodo_provider = Mock()
        client.decodo_provider.fetch_transcript.return_value = ("decodo words", "de")

        result = client._try_decodo_provider("anotherVid")

        assert result == ("decodo words", "de")
        client.decodo_provider.fetch_transcript.assert_called_once_with("anotherVid")


class TestFetchMetadata:
    """Metadata fetching with provider and placeholder fallback."""

    @staticmethod
    def make_client(tmp_path: Path) -> YouTubeClient:
        return YouTubeClient(TestYouTubeClientConstruction.make_settings(tmp_path))

    def test_uses_metadata_provider_result(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = self.make_client(tmp_path)
        client.metadata_provider = Mock()
        client.metadata_provider.fetch_metadata.return_value = {
            "title": "Real Title",
            "channel_name": "Real Channel",
        }

        caplog.set_level(logging.DEBUG, logger="obsidian_ai_tools.youtube")
        metadata = client._fetch_metadata("vid")

        assert metadata == {"title": "Real Title", "channel_name": "Real Channel"}
        client.metadata_provider.fetch_metadata.assert_called_once_with("vid")
        _assert_logged(caplog, "Fetching metadata from YouTube API for vid")
        _assert_logged(caplog, "Successfully fetched metadata from YouTube API for vid")

    def test_placeholder_when_provider_missing(self, tmp_path: Path) -> None:
        client = self.make_client(tmp_path)
        assert client.metadata_provider is None

        assert client._fetch_metadata("vid99") == {
            "title": "Video vid99",
            "channel_name": "Unknown Channel",
        }

    def test_placeholder_when_provider_fails(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = self.make_client(tmp_path)
        client.metadata_provider = Mock()
        client.metadata_provider.fetch_metadata.side_effect = Exception("boom")

        caplog.set_level(logging.WARNING, logger="obsidian_ai_tools.youtube")
        metadata = client._fetch_metadata("vid88")

        assert metadata == {"title": "Video vid88", "channel_name": "Unknown Channel"}
        _assert_logged(caplog, "Using placeholder metadata for vid88")
        _assert_logged(caplog, "YouTube API metadata fetch failed for vid88: boom")


class TestGetVideoMetadata:
    """End-to-end video metadata assembly with caching and validation."""

    @staticmethod
    def make_client(tmp_path: Path) -> YouTubeClient:
        return YouTubeClient(TestYouTubeClientConstruction.make_settings(tmp_path))

    def test_full_flow_builds_metadata(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        transcript = "real title " + " ".join(f"word{i}" for i in range(198))
        monkeypatch.setattr(
            client.unofficial_provider,
            "fetch_transcript",
            Mock(return_value=(transcript, "fr")),
        )
        client.metadata_provider = Mock()
        client.metadata_provider.fetch_metadata.return_value = {
            "title": "Real Title",
            "channel_name": "Channel X",
        }

        caplog.set_level(logging.INFO, logger="obsidian_ai_tools.youtube")
        result = client.get_video_metadata("https://youtube.com/watch?v=fullflow")

        assert isinstance(result, VideoMetadata)
        assert result.video_id == "fullflow"
        assert result.title == "Real Title"
        assert result.channel_name == "Channel X"
        assert result.url == "https://youtube.com/watch?v=fullflow"
        assert result.transcript == transcript
        assert result.source_language == "fr"
        assert result.provider_used == "direct"
        _assert_logged(caplog, "Cache MISS for fullflow - fetching from providers")
        _assert_logged(caplog, "Cached result for fullflow (provider: direct)")

    def test_cache_hit_short_circuits(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = self.make_client(tmp_path)
        cached = VideoMetadata(
            video_id="cachedvid",
            title="Cached",
            channel_name="Channel",
            url="https://youtube.com/watch?v=cachedvid",
            transcript="cached transcript words " * 20,
            source_language="en",
            provider_used="direct",
        )

        caplog.set_level(logging.INFO, logger="obsidian_ai_tools.youtube")
        with patch.object(client.cache, "get", return_value=cached) as mock_get:
            result = client.get_video_metadata("https://youtube.com/watch?v=cachedvid")

        assert result is cached
        mock_get.assert_called_once_with("cachedvid")
        _assert_logged(caplog, "Cache HIT for cachedvid")

    def test_forwards_provider_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self.make_client(tmp_path)
        fetch_metadata = Mock(return_value={"title": "T", "channel_name": "C"})
        fetch_transcript = Mock(
            return_value=(" ".join(f"word{i}" for i in range(200)), "en", "direct")
        )
        monkeypatch.setattr(client, "_fetch_metadata", fetch_metadata)
        monkeypatch.setattr(client, "_fetch_transcript_with_fallback", fetch_transcript)

        client.get_video_metadata(
            "https://youtube.com/watch?v=ord1", provider_order="supadata,decodo"
        )

        fetch_metadata.assert_called_once_with("ord1")
        fetch_transcript.assert_called_once_with("ord1", "supadata,decodo")

    def test_raises_on_low_quality_transcript(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        client.metadata_provider = Mock()
        client.metadata_provider.fetch_metadata.return_value = {
            "title": "Whatever Title",
            "channel_name": "C",
        }
        monkeypatch.setattr(
            client.unofficial_provider, "fetch_transcript", Mock(return_value=("short", "en"))
        )

        with pytest.raises(TranscriptUnavailableError) as exc:
            client.get_video_metadata("https://youtube.com/watch?v=qvid")

        assert str(exc.value) == (
            "Transcript quality too low for qvid: Transcript too short (5 chars, minimum 100)"
        )

    def test_raises_on_irrelevant_transcript(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self.make_client(tmp_path)
        transcript = " ".join(f"word{i}" for i in range(200))
        client.metadata_provider = Mock()
        client.metadata_provider.fetch_metadata.return_value = {
            "title": "Quantum SpaceX Rocket 2026",
            "channel_name": "C",
        }
        monkeypatch.setattr(
            client.unofficial_provider, "fetch_transcript", Mock(return_value=(transcript, "en"))
        )

        caplog.set_level(logging.WARNING, logger="obsidian_ai_tools.youtube")
        with pytest.raises(TranscriptUnavailableError) as exc:
            client.get_video_metadata("https://youtube.com/watch?v=irrel")

        assert str(exc.value) == (
            "Transcript content does not match video title for irrel. "
            "This may indicate corrupted or mismatched transcript data."
        )
        _assert_logged(
            caplog,
            "Transcript appears irrelevant to video title for irrel. "
            "Title: Quantum SpaceX Rocket 2026",
        )

    def test_module_level_get_video_metadata(self) -> None:
        """The convenience wrapper builds a default client and delegates."""
        with patch("obsidian_ai_tools.youtube.YouTubeClient") as mock_client_class:
            instance = mock_client_class.return_value
            instance.get_video_metadata.return_value = "metadata-result"

            result = module_get_video_metadata("https://youtu.be/abc12345")

        assert result == "metadata-result"
        mock_client_class.assert_called_once_with()
        instance.get_video_metadata.assert_called_once_with("https://youtu.be/abc12345")


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_basic_sanitization(self) -> None:
        """Test basic character sanitization."""
        result = sanitize_filename("Hello World")
        assert result == "hello-world"

    def test_special_characters_removed(self) -> None:
        """Test that special characters are removed."""
        result = sanitize_filename('Test: Video | "Special" <Chars>')
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result
        assert '"' not in result

    def test_multiple_spaces_collapsed(self) -> None:
        """Test that multiple spaces are collapsed to single hyphen."""
        result = sanitize_filename("Multiple    Spaces    Here")
        assert result == "multiple-spaces-here"

    def test_length_limit(self) -> None:
        """Test that long titles are truncated."""
        long_title = "A" * 150
        result = sanitize_filename(long_title, max_length=100)
        assert len(result) <= 100

    def test_empty_string_fallback(self) -> None:
        """Test that empty string returns fallback."""
        result = sanitize_filename("")
        assert result == "untitled-note"

    def test_only_special_chars_fallback(self) -> None:
        """Test that string with only special chars returns fallback."""
        result = sanitize_filename("***///:::<<<")
        assert result == "untitled-note"
