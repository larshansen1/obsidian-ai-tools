"""Tests for the web content provider (Trafilatura with Supadata fallback).

Targets every branch of ``providers/web.py``:
- __init__ wiring (user agent, Supadata endpoint)
- validate() URL gating
- _fetch_raw / _fetch_direct / _fetch_supadata extraction mapping and errors
- _check_raw_content GitHub blob and raw-content detection
- _ingest orchestration, provider-attempt telemetry, and fallback behavior

All tests are hermetic: HTTP, trafilatura, clock, database and rate limiter are
mocked so nothing touches the network or sleeps.
"""

import json
import logging
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from obsidian_ai_tools.providers.web import WebProvider

SRC = "https://example.com/article"
RAW_URL = "https://raw.githubusercontent.com/user/repo/main/README.md"

WEB_LOGGER = "obsidian_ai_tools.providers.web"


@pytest.fixture
def provider() -> WebProvider:
    """Create a WebProvider with the isolated test settings."""
    return WebProvider()


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return deterministic, incrementing timestamps from ``time.monotonic``.

    Every call returns ``1.0`` more than the previous, so an attempt measured
    as ``monotonic() - start`` is always exactly ``1.0`` seconds. This lets
    tests assert precise durations and kill arithmetic mutants of the
    telemetry timing.
    """
    state = {"t": 0.0}

    def _monotonic() -> float:
        state["t"] += 1.0
        return state["t"]

    monkeypatch.setattr("time.monotonic", _monotonic)


def _mock_db() -> MagicMock:
    """Return a fake get_db() object recording provider attempts."""
    return MagicMock()


def _text_response(text: str) -> MagicMock:
    """Build a fake requests.Response carrying ``text``."""
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def _json_response(payload: dict) -> MagicMock:
    """Build a fake requests.Response carrying JSON ``payload``."""
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


class TestWebProviderInit:
    """Provider construction wiring."""

    def test_init_wires_user_agent_and_supadata(self) -> None:
        """Constructor configures trafilatura and reads Supadata settings."""
        config = MagicMock()
        settings = MagicMock(supadata_key="secret-key-123")

        with (
            patch("obsidian_ai_tools.providers.web.use_config", return_value=config),
            patch("obsidian_ai_tools.providers.web.get_settings", return_value=settings),
        ):
            constructed = WebProvider()

        config.set.assert_called_once_with(
            "DEFAULT", "USER_AGENT", "Mozilla/5.0 (compatible; ObsidianAI/1.0)"
        )
        assert constructed.supadata_key == "secret-key-123"
        assert constructed.supadata_url == "https://api.supadata.ai/v1/web/scrape"


class TestWebProviderValidate:
    """URL acceptance rules."""

    def test_accepts_http_and_https(self, provider: WebProvider) -> None:
        """Both supported schemes validate."""
        assert provider.validate("http://example.com/page")
        assert provider.validate("https://example.com/page")

    def test_rejects_non_http_schemes(self, provider: WebProvider) -> None:
        """FTP and other schemes are not valid for the web provider."""
        assert not provider.validate("ftp://example.com/file")
        assert not provider.validate("example.com/page")

    def test_rejects_youtube_links(self, provider: WebProvider) -> None:
        """YouTube URLs are routed to the dedicated YouTube provider."""
        assert not provider.validate("https://youtube.com/watch?v=abc123")
        assert not provider.validate("https://www.youtube.com/watch?v=abc123")
        assert not provider.validate("https://youtu.be/abc123")

    def test_youtube_block_is_case_sensitive(self, provider: WebProvider) -> None:
        """'youtube.com' is only blocked in the exact lowercase form."""
        assert provider.validate("https://YOUTUBE.COM/watch?v=abc123")


class TestWebProviderFetchRaw:
    """Direct raw-content fetching."""

    def test_fetch_raw_success_maps_all_fields(self, provider: WebProvider) -> None:
        """Raw file content is fetched and mapped to the expected dict."""
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value = _text_response("# Readme\n\nBody text")
            result = provider._fetch_raw(RAW_URL)

        mock_get.assert_called_once_with(RAW_URL, timeout=30)
        assert result == {
            "content": "# Readme\n\nBody text",
            "title": "README.md",
            "author": "Unknown",
            "date": None,
            "site_name": "Raw Source",
            "url": RAW_URL,
        }

    def test_fetch_raw_trailing_slash_uses_default_title(self, provider: WebProvider) -> None:
        """URLs ending in '/' have no basename, so the default title applies."""
        url = "https://example.com/files/"
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value = _text_response("content")
            result = provider._fetch_raw(url)

        assert result["title"] == "Raw Content"
        assert result["content"] == "content"

    def test_fetch_raw_empty_content_exact_message(self, provider: WebProvider) -> None:
        """Whitespace-only bodies raise ValueError with an exact message."""
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value = _text_response("   ")
            with pytest.raises(ValueError) as exc_info:
                provider._fetch_raw("https://example.com/empty.txt")

        assert str(exc_info.value) == "Empty content received"


class TestWebProviderFetchSupadata:
    """Supadata API fallback fetching."""

    def test_fetch_supadata_success_maps_all_fields(self, provider: WebProvider) -> None:
        """API response is requested with key/params and mapped exactly."""
        provider.supadata_key = "test-key"
        payload = {"content": "Body", "name": "Named", "description": "Described"}

        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value = _json_response(payload)
            result = provider._fetch_supadata(SRC)

        mock_get.assert_called_once_with(
            provider.supadata_url,
            headers={"x-api-key": "test-key"},
            params={"url": SRC},
            timeout=30,
        )
        assert result == {
            "content": "Body",
            "title": "Named",
            "author": "Unknown Author",
            "date": None,
            "site_name": "Described",
            "url": SRC,
        }

    def test_fetch_supadata_default_title_and_site(self, provider: WebProvider) -> None:
        """Missing name/description fall back to stable defaults."""
        provider.supadata_key = "test-key"
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value = _json_response({"content": "Body"})
            result = provider._fetch_supadata(SRC)

        assert result["title"] == "Untitled Web Page"
        assert result["site_name"] == "Web Source"
        assert result["author"] == "Unknown Author"

    def test_fetch_supadata_no_content_exact_message(self, provider: WebProvider) -> None:
        """Responses without article text raise ValueError with an exact message."""
        provider.supadata_key = "test-key"
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value = _json_response({"name": "No Body"})
            with pytest.raises(ValueError) as exc_info:
                provider._fetch_supadata(SRC)

        assert str(exc_info.value) == "Supadata returned no content"


class TestWebProviderFetchDirect:
    """Trafilatura-based direct extraction."""

    def test_fetch_direct_success_maps_all_fields(self, provider: WebProvider) -> None:
        """Downloaded HTML is extracted with fixed options and mapped exactly."""
        html = "<html><body>article</body></html>"
        payload = {
            "text": "Body text",
            "title": "The Title",
            "author": "The Author",
            "date": "2024-01-02",
            "sitename": "The Site",
        }

        with (
            patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url") as mock_fetch,
            patch("obsidian_ai_tools.providers.web.trafilatura.extract") as mock_extract,
        ):
            mock_fetch.return_value = html
            mock_extract.return_value = json.dumps(payload)
            result = provider._fetch_direct(SRC)

        mock_fetch.assert_called_once_with(SRC)
        mock_extract.assert_called_once_with(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            output_format="json",
            with_metadata=True,
        )
        assert result == {
            "content": "Body text",
            "title": "The Title",
            "author": "The Author",
            "date": "2024-01-02",
            "site_name": "The Site",
            "url": SRC,
        }

    def test_fetch_direct_default_site_name(self, provider: WebProvider) -> None:
        """Missing sitename/hostname metadata falls back to Web Source."""
        with (
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.fetch_url",
                return_value="<html />",
            ),
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.extract",
                return_value=json.dumps({"text": "Body text"}),
            ),
        ):
            result = provider._fetch_direct(SRC)

        assert result is not None
        assert result["title"] == "Untitled Web Page"
        assert result["author"] == "Unknown Author"
        assert result["site_name"] == "Web Source"

    def test_fetch_direct_download_failure_warns(
        self, provider: WebProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Failed downloads return None and log the URL."""
        caplog.set_level(logging.WARNING, logger=WEB_LOGGER)
        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None):
            result = provider._fetch_direct(SRC)

        assert result is None
        assert f"Trafilatura failed to download {SRC}" in caplog.messages

    def test_fetch_direct_extract_failure_warns(
        self, provider: WebProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Empty trafilatura output returns None and logs a warning."""
        caplog.set_level(logging.WARNING, logger=WEB_LOGGER)
        with (
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.fetch_url",
                return_value="<html />",
            ),
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.extract",
                return_value=None,
            ),
        ):
            result = provider._fetch_direct(SRC)

        assert result is None
        assert "Trafilatura failed to extract content" in caplog.messages

    def test_fetch_direct_missing_text_warns(
        self, provider: WebProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Extraction without a text field returns None and logs a warning."""
        caplog.set_level(logging.WARNING, logger=WEB_LOGGER)
        with (
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.fetch_url",
                return_value="<html />",
            ),
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.extract",
                return_value=json.dumps({"title": "No Body"}),
            ),
        ):
            result = provider._fetch_direct(SRC)

        assert result is None
        assert "No text content extracted" in caplog.messages


class TestWebProviderCheckRawContent:
    """GitHub blob conversion and raw URL detection."""

    def test_github_blob_converts_to_raw(
        self, provider: WebProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Blob URLs are rewritten to raw.githubusercontent.com."""
        caplog.set_level(logging.INFO, logger=WEB_LOGGER)
        blob_url = "https://github.com/user/repo/blob/main/README.md"

        with patch.object(
            provider,
            "_fetch_raw",
            return_value={"title": "README.md", "url": RAW_URL},
        ) as mock_fetch_raw:
            result = provider._check_raw_content(blob_url)

        mock_fetch_raw.assert_called_once_with(RAW_URL)
        assert result == {"title": "README.md", "url": RAW_URL}
        assert f"Converting GitHub blob URL to raw: {RAW_URL}" in caplog.messages

    def test_requires_both_github_domain_and_blob_path(self, provider: WebProvider) -> None:
        """Neither github.com without /blob/ nor /blob/ without github.com match."""
        raw_result = {"content": "x", "url": "y"}

        with patch.object(provider, "_fetch_raw", return_value=raw_result) as mock_fetch_raw:
            assert provider._check_raw_content("https://github.com/user/repo") is None
            mock_fetch_raw.assert_not_called()

        with patch.object(provider, "_fetch_raw", return_value=raw_result) as mock_fetch_raw:
            assert provider._check_raw_content("https://example.com/a/blob/b") is None
            mock_fetch_raw.assert_not_called()

    def test_raw_extensions_fetch_directly(
        self, provider: WebProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """.md, .markdown and .txt URLs are fetched as raw content."""
        caplog.set_level(logging.INFO, logger=WEB_LOGGER)
        for extension in (".md", ".markdown", ".txt"):
            url = f"https://example.com/notes{extension}"
            with patch.object(provider, "_fetch_raw", return_value={"ok": True}) as mock_raw:
                result = provider._check_raw_content(url)
            mock_raw.assert_called_once_with(url)
            assert result == {"ok": True}

        assert "Detected raw content URL, attempting direct fetch" in caplog.messages

    def test_raw_githubusercontent_without_extension(self, provider: WebProvider) -> None:
        """raw.githubusercontent.com URLs are raw even without an extension."""
        url = "https://raw.githubusercontent.com/user/repo/main/notes"
        with patch.object(provider, "_fetch_raw", return_value={"ok": True}) as mock_raw:
            result = provider._check_raw_content(url)

        mock_raw.assert_called_once_with(url)
        assert result == {"ok": True}

    def test_blob_fetch_failure_falls_back(
        self, provider: WebProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing raw fetch for a blob falls back to standard extraction."""
        caplog.set_level(logging.WARNING, logger=WEB_LOGGER)
        with patch.object(provider, "_fetch_raw", side_effect=requests.exceptions.Timeout("slow")):
            result = provider._check_raw_content("https://github.com/user/repo/blob/main/f.md")

        assert result is None
        assert "GitHub raw fetch failed: slow. Falling back to standard extraction." in (
            caplog.messages
        )

    def test_raw_fetch_failure_falls_back(
        self, provider: WebProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing raw fetch for a raw URL falls back to standard extraction."""
        caplog.set_level(logging.WARNING, logger=WEB_LOGGER)
        with patch.object(provider, "_fetch_raw", side_effect=ValueError("bad")):
            result = provider._check_raw_content("https://example.com/notes.md")

        assert result is None
        assert "Raw fetch failed: bad. Falling back to standard extraction." in caplog.messages


class TestWebProviderIngest:
    """_ingest orchestration, telemetry and fallback behavior."""

    def test_captured_content_short_circuits(self, provider: WebProvider, fake_clock: None) -> None:
        """Browser-captured content skips fetching and is recorded as captured."""
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url") as mock_fetch,
        ):
            result = provider._ingest(
                SRC, captured_content="  hello\nworld  ", captured_title="Chat Title"
            )

        assert result.content == "hello\nworld"
        assert result.title == "Chat Title"
        assert result.author == "Unknown Author"
        assert result.published_date is None
        assert result.site_name == "Browser Capture"
        assert result.url == SRC
        mock_fetch.assert_not_called()
        db.record_provider_attempt.assert_called_once_with(
            "web", "captured", "success", 1.0, None, SRC
        )

    def test_captured_content_default_title(self, provider: WebProvider, fake_clock: None) -> None:
        """Captured content without a title gets the default Captured Chat title."""
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url"),
        ):
            result = provider._ingest(SRC, captured_content="hello")

        assert result.title == "Captured Chat"
        assert result.content == "hello"
        db.record_provider_attempt.assert_called_once_with(
            "web", "captured", "success", 1.0, None, SRC
        )

    def test_raw_path_records_success(self, provider: WebProvider, fake_clock: None) -> None:
        """Raw content extraction records a web/raw/success attempt."""
        db = _mock_db()
        raw = {
            "content": "# Raw body",
            "title": "f.md",
            "author": "Unknown",
            "date": None,
            "site_name": "Raw Source",
            "url": RAW_URL,
        }
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web._limiter") as mock_limiter,
            patch.object(provider, "_check_raw_content", return_value=raw),
        ):
            result = provider._ingest(SRC)

        assert result.title == "f.md"
        assert result.content == "# Raw body"
        mock_limiter.wait.assert_called_once_with(SRC)
        db.record_provider_attempt.assert_called_once_with("web", "raw", "success", 1.0, None, SRC)

    def test_primary_success_path(
        self, provider: WebProvider, fake_clock: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Successful trafilatura extraction records web/primary/success."""
        caplog.set_level(logging.INFO, logger=WEB_LOGGER)
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web._limiter"),
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.fetch_url",
                return_value="<html />",
            ),
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.extract",
                return_value=json.dumps({"text": "Article body"}),
            ),
        ):
            result = provider._ingest(SRC)

        assert result.content == "Article body"
        assert result.title == "Untitled Web Page"
        db.record_provider_attempt.assert_called_once_with(
            "web", "primary", "success", 1.0, None, SRC
        )
        assert "Successfully fetched article using Trafilatura" in caplog.messages

    def test_primary_failure_no_fallback(self, provider: WebProvider, fake_clock: None) -> None:
        """Failed extraction with no Supadata key raises the no-fallback error."""
        provider.supadata_key = None
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web._limiter"),
            patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                provider._ingest(SRC)

        assert str(exc_info.value) == (
            f"Failed to fetch content from {SRC} and no fallback configured"
        )
        db.record_provider_attempt.assert_called_once_with(
            "web", "primary", "failure", 1.0, None, SRC
        )

    def test_primary_exception_records_error_type(
        self, provider: WebProvider, fake_clock: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An exploding fetch records the exception class name as error_type."""
        provider.supadata_key = None
        caplog.set_level(logging.WARNING, logger=WEB_LOGGER)
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web._limiter"),
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.fetch_url",
                side_effect=ValueError("bad html"),
            ),
        ):
            with pytest.raises(RuntimeError):
                provider._ingest(SRC)

        db.record_provider_attempt.assert_called_once_with(
            "web", "primary", "failure", 1.0, "ValueError", SRC
        )
        assert "Direct extraction failed: bad html. Attempting fallback." in caplog.messages

    def test_fallback_success(
        self, provider: WebProvider, fake_clock: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Supadata rescues a failed primary extraction and records both attempts."""
        provider.supadata_key = "test-key"
        caplog.set_level(logging.INFO, logger=WEB_LOGGER)
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web._limiter"),
            patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None),
            patch("obsidian_ai_tools.providers.web.requests.get") as mock_get,
        ):
            mock_get.return_value = _json_response(
                {"content": "Supadata body", "name": "Supadata Title"}
            )
            result = provider._ingest(SRC)

        assert result.content == "Supadata body"
        assert result.title == "Supadata Title"
        mock_get.assert_called_once_with(
            provider.supadata_url,
            headers={"x-api-key": "test-key"},
            params={"url": SRC},
            timeout=30,
        )
        db.record_provider_attempt.assert_has_calls(
            [
                call("web", "primary", "failure", 1.0, None, SRC),
                call("web", "fallback", "success", 1.0, None, SRC),
            ]
        )
        assert db.record_provider_attempt.call_count == 2
        assert "Falling back to Supadata extraction" in caplog.messages
        assert "Successfully fetched article using Supadata" in caplog.messages

    def test_fallback_failure_raises(
        self, provider: WebProvider, fake_clock: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing Supadata fallback raises RuntimeError and records the failure."""
        provider.supadata_key = "test-key"
        caplog.set_level(logging.ERROR, logger=WEB_LOGGER)
        db = _mock_db()
        with (
            patch("obsidian_ai_tools.providers.web.get_db", return_value=db),
            patch("obsidian_ai_tools.providers.web._limiter"),
            patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None),
            patch(
                "obsidian_ai_tools.providers.web.requests.get",
                side_effect=requests.exceptions.ConnectionError("boom"),
            ),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                provider._ingest(SRC)

        assert str(exc_info.value) == f"Failed to fetch article from {SRC}: boom"
        db.record_provider_attempt.assert_has_calls(
            [
                call("web", "primary", "failure", 1.0, None, SRC),
                call("web", "fallback", "failure", 1.0, "ConnectionError", SRC),
            ]
        )
        assert db.record_provider_attempt.call_count == 2
        assert "Supadata extraction failed: boom" in caplog.messages
