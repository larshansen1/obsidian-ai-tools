"""Tests for the X (Twitter) thread provider.

Targets every branch of ``providers/x.py``:
- __init__ wiring (Supadata config)
- name / validate() URL gating
- extension-captured content path (no network, exact tweet splitting)
- Supadata fallback mapping, error wrapping and telemetry
- no-fallback error when no Supadata key is configured

All tests are hermetic: HTTP, clock, database and rate limiter are mocked.
"""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from obsidian_ai_tools.ingestion import default_prompt_version
from obsidian_ai_tools.models import ArticleMetadata
from obsidian_ai_tools.providers.factory import ProviderFactory
from obsidian_ai_tools.providers.web import WebProvider
from obsidian_ai_tools.providers.x import XThreadProvider

SRC = "https://x.com/user/status/123456789"
SEP = "\n\n---\n\n"


def _mock_db() -> MagicMock:
    """Return a fake get_db() object recording provider attempts."""
    return MagicMock()


def _json_response(payload: dict) -> MagicMock:
    """Build a fake requests.Response carrying JSON ``payload``."""
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


@pytest.fixture
def provider() -> XThreadProvider:
    """Create an XThreadProvider with the isolated test settings."""
    return XThreadProvider()


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return deterministic, incrementing timestamps from ``time.monotonic``."""
    state = {"t": 0.0}

    def _monotonic() -> float:
        state["t"] += 1.0
        return state["t"]

    monkeypatch.setattr("time.monotonic", _monotonic)


class TestXProviderInit:
    """Provider construction wiring."""

    def test_name_is_x(self, provider: XThreadProvider) -> None:
        """Provider name matches the prompt mapping key and telemetry label."""
        assert provider.name == "x"

    def test_init_reads_supadata_config(self) -> None:
        """Constructor reads Supadata key from settings."""
        settings = MagicMock(supadata_key="secret-key-123")
        with patch("obsidian_ai_tools.providers.x.get_settings", return_value=settings):
            constructed = XThreadProvider()

        assert constructed.supadata_key == "secret-key-123"
        assert constructed.supadata_url == "https://api.supadata.ai/v1/metadata"


class TestXProviderValidate:
    """URL acceptance rules."""

    def test_accepts_x_status_urls(self, provider: XThreadProvider) -> None:
        """x.com status URLs validate regardless of scheme or www."""
        assert provider.validate(SRC)
        assert provider.validate("http://x.com/user/status/123")
        assert provider.validate("https://www.x.com/user/status/123")

    def test_accepts_twitter_status_urls(self, provider: XThreadProvider) -> None:
        """twitter.com status URLs validate (same site as x.com)."""
        assert provider.validate("https://twitter.com/user/status/987654321")
        assert provider.validate("https://www.twitter.com/user/status/987654321")

    def test_rejects_non_status_pages(self, provider: XThreadProvider) -> None:
        """Home, user and non-numeric status pages are not thread sources."""
        assert not provider.validate("https://x.com/home")
        assert not provider.validate("https://x.com/user")
        assert not provider.validate("https://x.com/user/status/abc")

    def test_rejects_non_x_domains(self, provider: XThreadProvider) -> None:
        """Unrelated domains, including x.com-lookalike paths, are rejected."""
        assert not provider.validate("https://example.com/article")
        assert not provider.validate("https://example.com/x.com/status/1")
        assert not provider.validate("https://proxy-x.com/user/status/1")


class TestXProviderCaptured:
    """Extension-captured path: no network, telemetry recorded."""

    def _ingest(self, provider: XThreadProvider, db: MagicMock, **kwargs) -> object:
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x.requests.get") as mock_get,
        ):
            result = provider._ingest(SRC, **kwargs)
        mock_get.assert_not_called()
        return result

    def test_captured_content_maps_all_fields(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Captured content, author and date flow into thread metadata."""
        db = _mock_db()
        result = self._ingest(
            provider,
            db,
            captured_content=f"  first tweet{SEP}second tweet  ",
            captured_title="My Thread",
            captured_author="handle",
            captured_date="2026-09-07T09:00:00.000Z",
        )

        assert result.content == f"first tweet{SEP}second tweet"
        assert result.title == "My Thread"
        assert result.author == "handle"
        assert result.published_date == "2026-09-07T09:00:00.000Z"
        assert result.site_name == "X"
        assert result.url == SRC
        assert result.fetch_method == "extension"
        assert result.source_type == "x"
        assert result.engagement is None
        assert result.media_presence is None
        assert [tweet.text for tweet in result.tweets] == ["first tweet", "second tweet"]
        assert result.tweets[0].timestamp is None
        assert result.tweets[0].tweet_id is None
        db.record_provider_attempt.assert_called_once_with(
            "x", "extension", "success", 1.0, None, SRC
        )

    def test_captured_default_title_uses_author(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """No captured title -> @author on X."""
        db = _mock_db()
        result = self._ingest(provider, db, captured_content="hello", captured_author="handle")

        assert result.title == "@handle on X"
        assert [tweet.text for tweet in result.tweets] == ["hello"]

    def test_captured_default_title_without_author(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """No captured title or author -> X Thread."""
        db = _mock_db()
        result = self._ingest(provider, db, captured_content="hello")

        assert result.title == "X Thread"
        assert result.published_date is None

    def test_captured_without_separator_is_single_tweet(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Content with no separator still yields one ordered tweet."""
        db = _mock_db()
        result = self._ingest(provider, db, captured_content="just one tweet")

        assert [tweet.text for tweet in result.tweets] == ["just one tweet"]

    def test_blank_captured_content_falls_back_to_supadata(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Whitespace-only captured content does not short-circuit."""
        db = _mock_db()
        payload = {
            "id": "123456789",
            "title": None,
            "description": "Tweet body",
            "author": {"username": "handle", "displayName": "Display"},
            "createdAt": "2026-09-07T09:00:00.000Z",
        }
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter") as mock_limiter,
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                return_value=_json_response(payload),
            ) as mock_get,
        ):
            result = provider._ingest(SRC, captured_content="   ")

        mock_limiter.wait.assert_called_once_with(SRC)
        mock_get.assert_called_once()
        assert result.fetch_method == "supadata"
        assert result.content == "Tweet body"


class TestXProviderSupadata:
    """URL-only fallback via the Supadata metadata endpoint."""

    BASE_PAYLOAD = {
        "platform": "twitter",
        "type": "post",
        "id": "123456789",
        "title": "Thread title",
        "description": "First tweet text",
        "author": {"username": "handle", "displayName": "Display Name"},
        "stats": {"views": 1000, "likes": 50, "comments": 5, "shares": 2},
        "media": {"type": "post"},
        "createdAt": "2026-09-07T09:00:00.000Z",
    }

    def test_success_maps_all_fields(self, provider: XThreadProvider, fake_clock: None) -> None:
        """Full metadata mapping from the Supadata response."""
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter") as mock_limiter,
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                return_value=_json_response(dict(self.BASE_PAYLOAD)),
            ) as mock_get,
        ):
            result = provider._ingest(SRC)

        mock_get.assert_called_once_with(
            "https://api.supadata.ai/v1/metadata",
            headers={"x-api-key": "test-supadata-key"},
            params={"url": SRC},
            timeout=30,
        )
        mock_limiter.wait.assert_called_once_with(SRC)
        assert result.content == "First tweet text"
        assert result.title == "Thread title"
        assert result.author == "handle"
        assert result.published_date == "2026-09-07T09:00:00.000Z"
        assert result.site_name == "X"
        assert result.source_type == "x"
        assert result.fetch_method == "supadata"
        assert result.engagement == {"views": 1000, "likes": 50, "comments": 5, "shares": 2}
        assert result.media_presence is None  # media.type "post" is not an attachment
        assert len(result.tweets) == 1
        assert result.tweets[0].text == "First tweet text"
        assert result.tweets[0].timestamp == "2026-09-07T09:00:00.000Z"
        assert result.tweets[0].tweet_id == "123456789"
        db.record_provider_attempt.assert_called_once_with(
            "x", "supadata", "success", 1.0, None, SRC
        )

    def test_missing_title_defaults_to_author(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """No title -> @handle on X when the author is known."""
        db = _mock_db()
        payload = dict(self.BASE_PAYLOAD, title=None)
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter"),
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                return_value=_json_response(payload),
            ),
        ):
            result = provider._ingest(SRC)

        assert result.title == "@handle on X"

    def test_null_stats_and_video_media(self, provider: XThreadProvider, fake_clock: None) -> None:
        """All-null stats produce no engagement; video media is noted as text."""
        db = _mock_db()
        payload = dict(
            self.BASE_PAYLOAD,
            stats={"views": None, "likes": None, "comments": None, "shares": None},
            media={"type": "video"},
        )
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter"),
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                return_value=_json_response(payload),
            ),
        ):
            result = provider._ingest(SRC)

        assert result.engagement is None
        assert result.media_presence == "video attachment"

    def test_no_content_raises_exact_message(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Response without description or title is a clean error."""
        db = _mock_db()
        payload = dict(self.BASE_PAYLOAD, description=None, title=None)
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter"),
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                return_value=_json_response(payload),
            ),
            pytest.raises(RuntimeError) as excinfo,
        ):
            provider._ingest(SRC)

        assert str(excinfo.value) == (
            f"Failed to fetch X thread from {SRC}: Supadata returned no text content"
        )
        db.record_provider_attempt.assert_called_once_with(
            "x", "supadata", "failure", 1.0, "ValueError", SRC
        )

    def test_request_failure_raises_and_records_error_type(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Network errors surface as RuntimeError with the error type recorded."""
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter"),
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                side_effect=requests.Timeout("read timeout"),
            ),
            pytest.raises(RuntimeError) as excinfo,
        ):
            provider._ingest(SRC)

        assert str(excinfo.value) == f"Failed to fetch X thread from {SRC}: read timeout"
        db.record_provider_attempt.assert_called_once_with(
            "x", "supadata", "failure", 1.0, "Timeout", SRC
        )

    def test_missing_key_raises_no_fallback_message(self, fake_clock: None) -> None:
        """No Supadata key configured -> explicit no-fallback error, no attempt."""
        db = _mock_db()
        settings = MagicMock(supadata_key=None)
        with (
            patch("obsidian_ai_tools.providers.x.get_settings", return_value=settings),
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter") as mock_limiter,
            patch("obsidian_ai_tools.providers.x.requests.get") as mock_get,
            pytest.raises(RuntimeError) as excinfo,
        ):
            no_key_provider = XThreadProvider()
            no_key_provider._ingest(SRC)

        assert str(excinfo.value) == (
            f"Failed to fetch X thread from {SRC} and no fallback configured"
        )
        mock_limiter.wait.assert_called_once_with(SRC)
        mock_get.assert_not_called()
        db.record_provider_attempt.assert_not_called()


class TestXProviderLinkOnly:
    """Bare-link content (X Articles/Longform) expands via WebProvider."""

    LINK = "https://t.co/oxa2EB0xtY"
    RESOLVED = "https://x.com/i/article/2095378321588826112"
    BODY = "Applied AI Doesn't Work - full article body"

    def _article(self) -> ArticleMetadata:
        """ArticleMetadata as WebProvider returns it for the resolved target."""
        return ArticleMetadata(
            content=self.BODY,
            title="Applied AI Doesn't Work",
            author="Unknown Author",
            url=self.RESOLVED,
        )

    def _redirect_response(self) -> MagicMock:
        """Fake requests.Response carrying the resolved URL after redirects."""
        response = MagicMock()
        response.url = self.RESOLVED
        return response

    def test_captured_bare_link_expands_via_web(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Extension-captured content that is only a link fetches the target."""
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                return_value=self._redirect_response(),
            ) as mock_get,
            patch("obsidian_ai_tools.providers.x.WebProvider") as mock_web,
        ):
            mock_web.return_value.ingest.return_value = self._article()
            result = provider._ingest(SRC, captured_content=self.LINK, captured_author="handle")

        mock_get.assert_called_once_with(self.LINK, allow_redirects=True, stream=True, timeout=30)
        mock_web.assert_called_once_with()
        mock_web.return_value.ingest.assert_called_once_with(self.RESOLVED)
        assert result.content == self.BODY
        assert result.title == "Applied AI Doesn't Work"
        assert result.author == "handle"
        assert result.url == SRC
        assert result.site_name == "X"
        assert result.fetch_method == "web-expand"
        assert result.source_type == "x"
        assert [tweet.text for tweet in result.tweets] == [self.BODY]
        db.record_provider_attempt.assert_has_calls(
            [
                call("x", "extension", "success", 1.0, None, SRC),
                call("x", "expand", "success", 1.0, None, SRC),
            ]
        )

    def test_supadata_bare_link_expands_via_web(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Supadata description that is only a link fetches the target."""
        db = _mock_db()
        payload = {
            "id": "123456789",
            "title": None,
            "description": self.LINK,
            "author": {"username": "handle", "displayName": "Display Name"},
            "createdAt": "2026-09-07T09:00:00.000Z",
        }
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter") as mock_limiter,
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                side_effect=[_json_response(payload), self._redirect_response()],
            ) as mock_get,
            patch("obsidian_ai_tools.providers.x.WebProvider") as mock_web,
        ):
            mock_web.return_value.ingest.return_value = self._article()
            result = provider._ingest(SRC)

        mock_get.assert_has_calls(
            [
                call(
                    "https://api.supadata.ai/v1/metadata",
                    headers={"x-api-key": "test-supadata-key"},
                    params={"url": SRC},
                    timeout=30,
                ),
                call(self.LINK, allow_redirects=True, stream=True, timeout=30),
            ]
        )
        mock_limiter.wait.assert_called_once_with(SRC)
        mock_web.assert_called_once_with()
        mock_web.return_value.ingest.assert_called_once_with(self.RESOLVED)
        assert result.content == self.BODY
        assert result.title == "Applied AI Doesn't Work"
        assert result.author == "handle"
        assert result.url == SRC
        assert result.site_name == "X"
        assert result.fetch_method == "web-expand"
        assert [tweet.text for tweet in result.tweets] == [self.BODY]
        db.record_provider_attempt.assert_has_calls(
            [
                call("x", "expand", "success", 1.0, None, SRC),
                call("x", "supadata", "success", 3.0, None, SRC),
            ]
        )

    def test_captured_bare_link_web_failure_raises_exact_message(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Failed target fetch in the captured path raises a clear error."""
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                return_value=self._redirect_response(),
            ),
            patch("obsidian_ai_tools.providers.x.WebProvider") as mock_web,
            pytest.raises(RuntimeError) as excinfo,
        ):
            mock_web.return_value.ingest.side_effect = RuntimeError("no fallback configured")
            provider._ingest(SRC, captured_content=self.LINK)

        assert str(excinfo.value) == (
            f"Failed to fetch X article content from {self.RESOLVED}: no fallback configured"
        )
        db.record_provider_attempt.assert_has_calls(
            [
                call("x", "extension", "success", 1.0, None, SRC),
                call("x", "expand", "failure", 1.0, "RuntimeError", SRC),
            ]
        )

    def test_supadata_bare_link_web_failure_raises_exact_message(
        self, provider: XThreadProvider, fake_clock: None
    ) -> None:
        """Failed target fetch in the metadata path is wrapped by _ingest."""
        db = _mock_db()
        payload = {
            "id": "123456789",
            "title": None,
            "description": self.LINK,
            "author": {"username": "handle", "displayName": "Display Name"},
        }
        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.x._limiter"),
            patch(
                "obsidian_ai_tools.providers.x.requests.get",
                side_effect=[_json_response(payload), self._redirect_response()],
            ),
            patch("obsidian_ai_tools.providers.x.WebProvider") as mock_web,
            pytest.raises(RuntimeError) as excinfo,
        ):
            mock_web.return_value.ingest.side_effect = ValueError("scrape blocked")
            provider._ingest(SRC)

        assert str(excinfo.value) == (
            f"Failed to fetch X thread from {SRC}: "
            f"Failed to fetch X article content from {self.RESOLVED}: scrape blocked"
        )
        db.record_provider_attempt.assert_has_calls(
            [
                call("x", "expand", "failure", 1.0, "ValueError", SRC),
                call("x", "supadata", "failure", 3.0, "RuntimeError", SRC),
            ]
        )


class TestXRouting:
    """Factory and prompt wiring."""

    def test_factory_routes_x_urls_to_x_provider(self) -> None:
        """x.com/twitter.com status URLs resolve to the X provider, not Web."""
        for source in (SRC, "https://twitter.com/user/status/1"):
            assert isinstance(ProviderFactory.get_provider(source), XThreadProvider)

    def test_web_provider_excludes_x_domains(self) -> None:
        """WebProvider no longer claims X URLs, lower- or upper-case."""
        web = WebProvider()
        assert not web.validate(SRC)
        assert not web.validate("https://twitter.com/user/status/1")

    def test_default_prompt_version_maps_x(self) -> None:
        """X requests default to the faithful-transcription prompt."""
        assert default_prompt_version("x") == "twitter_thread_v1"


class TestXEndToEnd:
    """Real provider through the shared ingestion pipeline (mocked LLM)."""

    def test_ingest_content_writes_x_note(self, tmp_path: Path, fake_clock: None) -> None:
        """Captured thread flows through routing, prompt selection and vault write."""
        from types import SimpleNamespace

        from obsidian_ai_tools.ingestion import (
            IngestionRequest,
            IngestionResult,
            ingest_content,
        )
        from obsidian_ai_tools.models import CostInfo, Note

        settings = SimpleNamespace(
            obsidian_vault_path=tmp_path,
            obsidian_inbox_folder="inbox",
            llm_model="test-model",
            openrouter_api_key="test-key",
            llm_base_url="https://openrouter.ai/api/v1",
            max_transcript_length=1234,
        )
        note = Note(
            title="Generated Thread Note",
            summary="Summary",
            tags=["x"],
            source_url=SRC,
            source_type="x",
            model="test-model",
            prompt_version="twitter_thread_v1",
        )
        cost = CostInfo(
            model="test-model",
            source_type="x",
            input_tokens=100,
            output_tokens=50,
            total_cost_usd=0.001,
            source_url=SRC,
        )
        note_path = tmp_path / "inbox" / "x-generated-note.md"
        db = _mock_db()

        with (
            patch("obsidian_ai_tools.providers.get_db", return_value=db),
            patch(
                "obsidian_ai_tools.ingestion.generate_note", return_value=(note, cost)
            ) as mock_generate,
            patch("obsidian_ai_tools.ingestion.write_note", return_value=note_path) as mock_write,
        ):
            result = ingest_content(
                IngestionRequest(
                    url=SRC,
                    captured_content=f"first tweet{SEP}second tweet",
                    captured_author="handle",
                    captured_date="2026-09-07T09:00:00.000Z",
                ),
                settings,  # type: ignore[arg-type]
            )

        assert result == IngestionResult(
            provider_name="x",
            prompt_version="twitter_thread_v1",
            metadata=result.metadata,
            note=note,
            file_path=note_path,
        )
        assert result.metadata.source_type == "x"
        assert result.metadata.fetch_method == "extension"
        assert result.metadata.author == "handle"
        assert [tweet.text for tweet in result.metadata.tweets] == [
            "first tweet",
            "second tweet",
        ]
        mock_generate.assert_called_once_with(
            metadata=result.metadata,
            model="test-model",
            api_key="test-key",
            existing_tags=None,
            max_content_length=1234,
            prompt_version="twitter_thread_v1",
            base_url="https://openrouter.ai/api/v1",
        )
        mock_write.assert_called_once_with(
            note=note, vault_path=tmp_path, inbox_folder="inbox", target_path=None
        )
        db.record_provider_attempt.assert_called_once_with(
            "x", "extension", "success", 1.0, None, SRC
        )
