"""Tests for the YouTubeProvider wrapper in providers/youtube.py.

The wrapper itself owns three behaviours worth asserting: URL validation, the
``provider_order`` forwarding out of ``**kwargs``, and the observability
bookkeeping around the client call. Everything else lives in
``obsidian_ai_tools.youtube`` and is covered by tests/providers/test_youtube_providers.py.

Patch targets differ by import style:

* ``YouTubeClient`` is imported *inside* ``_ingest``, so the statement re-runs on
  every call and the defining module is the only stable target
  (``obsidian_ai_tools.youtube.YouTubeClient``).
* ``get_db`` is a module-level import, so it is patched at the use site
  (``obsidian_ai_tools.providers.youtube.get_db``).
"""

from unittest.mock import MagicMock, patch

import pytest

from obsidian_ai_tools.models import VideoMetadata
from obsidian_ai_tools.providers.youtube import YouTubeProvider
from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError

URL = "https://www.youtube.com/watch?v=abc123"


def _metadata() -> VideoMetadata:
    """A minimal, valid result for the mocked client to return."""
    return VideoMetadata(
        video_id="abc123",
        title="Test Video",
        url=URL,
        transcript="Some transcript text.",
        channel_name="Test Channel",
        provider_used="direct",
    )


def _client_returning(result: VideoMetadata) -> MagicMock:
    client = MagicMock()
    client.get_video_metadata.return_value = result
    return client


def _client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.get_video_metadata.side_effect = exc
    return client


def test_provider_name_is_youtube() -> None:
    """The factory keys providers by name, so the literal matters."""
    assert YouTubeProvider().name == "youtube"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc123", True),
        ("https://youtube.com/shorts/abc123", True),
        ("https://youtu.be/abc123", True),
        ("http://m.youtube.com/watch?v=abc123", True),
        ("https://example.com/watch?v=abc123", False),
        ("https://vimeo.com/123456", False),
        ("/local/path/video.mp4", False),
    ],
)
def test_validate_matches_only_youtube_sources(source: str, expected: bool) -> None:
    """validate() gates which provider the factory selects for a source."""
    assert YouTubeProvider().validate(source) is expected


def test_ingest_returns_client_result_and_records_a_success_attempt() -> None:
    """A successful fetch is returned untouched and logged as primary/success."""
    metadata = _metadata()
    client = _client_returning(metadata)
    db = MagicMock()

    with (
        patch("obsidian_ai_tools.youtube.YouTubeClient", return_value=client) as client_cls,
        patch("obsidian_ai_tools.providers.youtube.get_db", return_value=db),
    ):
        result = YouTubeProvider()._ingest(URL)

    assert result is metadata
    # The production caller constructs the client with no settings argument.
    client_cls.assert_called_once_with()

    db.record_provider_attempt.assert_called_once()
    args = db.record_provider_attempt.call_args.args
    kwargs = db.record_provider_attempt.call_args.kwargs
    assert args[:3] == ("youtube", "primary", "success")
    assert isinstance(args[3], float)
    assert args[3] >= 0.0
    assert kwargs == {"url": URL}


def test_ingest_records_a_failure_attempt_and_reraises() -> None:
    """Observability must not swallow the error the caller needs to see."""
    error = TranscriptUnavailableError("all providers failed")
    db = MagicMock()

    with (
        patch("obsidian_ai_tools.youtube.YouTubeClient", return_value=_client_raising(error)),
        patch("obsidian_ai_tools.providers.youtube.get_db", return_value=db),
        pytest.raises(TranscriptUnavailableError, match="all providers failed"),
    ):
        YouTubeProvider()._ingest(URL)

    db.record_provider_attempt.assert_called_once()
    args = db.record_provider_attempt.call_args.args
    assert args[:3] == ("youtube", "primary", "failure")
    assert isinstance(args[3], float)
    assert args[3] >= 0.0
    assert args[4] == "TranscriptUnavailableError"
    assert args[5] == URL


@pytest.mark.parametrize("provider_order", ["supadata,direct", "decodo", None])
def test_ingest_forwards_provider_order_from_kwargs(provider_order: str | None) -> None:
    """provider_order is not a declared parameter; it is plucked from **kwargs.

    The None case is the default CLI path, where the client falls back to the
    configured order instead of an override.
    """
    client = _client_returning(_metadata())
    kwargs = {} if provider_order is None else {"provider_order": provider_order}

    with (
        patch("obsidian_ai_tools.youtube.YouTubeClient", return_value=client),
        patch("obsidian_ai_tools.providers.youtube.get_db", return_value=MagicMock()),
    ):
        YouTubeProvider()._ingest(URL, **kwargs)

    client.get_video_metadata.assert_called_once_with(URL, provider_order=provider_order)


def test_ingest_ignores_unrelated_kwargs() -> None:
    """Only provider_order is forwarded; other ingest options are dropped here."""
    client = _client_returning(_metadata())

    with (
        patch("obsidian_ai_tools.youtube.YouTubeClient", return_value=client),
        patch("obsidian_ai_tools.providers.youtube.get_db", return_value=MagicMock()),
    ):
        YouTubeProvider()._ingest(URL, captured_content="ignored", vault="ignored")

    client.get_video_metadata.assert_called_once_with(URL, provider_order=None)


def test_ingest_succeeds_when_observability_is_broken() -> None:
    """A dead DuckDB must never turn a good fetch into a failure."""
    metadata = _metadata()

    with (
        patch(
            "obsidian_ai_tools.youtube.YouTubeClient",
            return_value=_client_returning(metadata),
        ),
        patch(
            "obsidian_ai_tools.providers.youtube.get_db",
            side_effect=RuntimeError("observability database unavailable"),
        ),
    ):
        result = YouTubeProvider()._ingest(URL)

    assert result is metadata


def test_ingest_reraises_original_error_when_observability_is_broken() -> None:
    """On the failure path the ingest error wins over the observability error."""
    error = TranscriptUnavailableError("all providers failed")

    with (
        patch("obsidian_ai_tools.youtube.YouTubeClient", return_value=_client_raising(error)),
        patch(
            "obsidian_ai_tools.providers.youtube.get_db",
            side_effect=RuntimeError("observability database unavailable"),
        ),
        pytest.raises(TranscriptUnavailableError, match="all providers failed") as excinfo,
    ):
        YouTubeProvider()._ingest(URL)

    assert excinfo.value is error
