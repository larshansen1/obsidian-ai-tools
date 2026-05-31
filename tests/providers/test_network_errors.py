"""Tests for network error handling in providers.

Test Strategy:
- Test timeout handling for all providers (YouTube, PDF, Web)
- Test DNS failure handling
- Test SSL/TLS error handling
- Test connection errors

Note: We mock at the module level where requests is imported.
PDF Provider uses requests.get for direct download and requests.post for Supadata fallback.
"""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
import requests
from tenacity import wait_none

from obsidian_ai_tools.providers.base import BaseProvider
from obsidian_ai_tools.providers.pdf import PDFProvider
from obsidian_ai_tools.providers.web import WebProvider


@pytest.fixture(autouse=True)
def disable_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise retry attempts without production backoff delays."""
    retrying_ingest = cast(Any, BaseProvider.ingest).retry_with(wait=wait_none())
    monkeypatch.setattr(BaseProvider, "ingest", retrying_ingest)
    monkeypatch.setattr("obsidian_ai_tools.providers.pdf._limiter.wait", lambda _: None)
    monkeypatch.setattr("obsidian_ai_tools.providers.web._limiter.wait", lambda _: None)


class TestPDFProviderNetworkErrors:
    """Test network error handling for PDF provider."""

    @pytest.fixture
    def provider(self) -> PDFProvider:
        """Create a PDF provider instance."""
        return PDFProvider()

    def test_remote_pdf_timeout_no_fallback(self, provider: PDFProvider) -> None:
        """Handle timeout when downloading PDF with no fallback configured."""
        # Remove Supadata key to disable fallback
        provider.supadata_key = None

        with patch("obsidian_ai_tools.providers.pdf.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectTimeout("Connection timed out")

            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://example.com/document.pdf")

            assert "failed" in str(exc_info.value).lower()

    def test_remote_pdf_failure_retries_three_times(self, provider: PDFProvider) -> None:
        """Retry transient failures three times without sleeping in tests."""
        provider.supadata_key = None

        with patch("obsidian_ai_tools.providers.pdf.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectTimeout("Connection timed out")

            with pytest.raises(RuntimeError):
                provider.ingest("https://example.com/document.pdf")

            assert mock_get.call_count == 3

    def test_remote_pdf_timeout_fallback_also_fails(self, provider: PDFProvider) -> None:
        """Handle timeout when both direct download and fallback fail."""
        provider.supadata_key = "fake-key"  # Enable fallback so the Supadata path is exercised

        with patch("obsidian_ai_tools.providers.pdf.requests.get") as mock_get:
            with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
                mock_get.side_effect = requests.exceptions.ConnectTimeout("Connection timed out")
                mock_post.side_effect = requests.exceptions.ConnectTimeout(
                    "Supadata connection timed out"
                )

                with pytest.raises(requests.exceptions.ConnectTimeout):
                    provider.ingest("https://example.com/document.pdf")

    def test_remote_pdf_dns_failure_no_fallback(self, provider: PDFProvider) -> None:
        """Handle DNS resolution failure with no fallback."""
        provider.supadata_key = None

        with patch("obsidian_ai_tools.providers.pdf.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError(
                "Failed to resolve 'nonexistent-domain.example'"
            )

            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://nonexistent-domain.example/doc.pdf")

            assert "failed" in str(exc_info.value).lower()

    def test_remote_pdf_ssl_error_no_fallback(self, provider: PDFProvider) -> None:
        """Handle SSL certificate errors with no fallback."""
        provider.supadata_key = None

        with patch("obsidian_ai_tools.providers.pdf.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.SSLError(
                "SSL certificate verification failed"
            )

            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://invalid-cert.example/doc.pdf")

            assert "failed" in str(exc_info.value).lower()

    def test_remote_pdf_404_both_fail(self, provider: PDFProvider) -> None:
        """Handle 404 when both sources fail."""
        provider.supadata_key = "fake-key"  # Enable fallback so Supadata path is exercised

        with patch("obsidian_ai_tools.providers.pdf.requests.get") as mock_get:
            with patch("obsidian_ai_tools.providers.pdf.requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 404
                mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                    "404 Client Error: Not Found"
                )
                mock_get.return_value = mock_response
                mock_post.return_value = mock_response

                with pytest.raises(requests.exceptions.HTTPError):
                    provider.ingest("https://example.com/missing.pdf")

    def test_remote_pdf_connection_reset_no_fallback(self, provider: PDFProvider) -> None:
        """Handle connection reset by peer with no fallback."""
        provider.supadata_key = None

        with patch("obsidian_ai_tools.providers.pdf.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError("Connection reset by peer")

            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://example.com/doc.pdf")

            assert "failed" in str(exc_info.value).lower()


class TestWebProviderNetworkErrors:
    """Test network error handling for Web provider."""

    @pytest.fixture
    def provider(self) -> WebProvider:
        """Create a Web provider instance."""
        return WebProvider()

    def test_web_connection_timeout_all_fail(self, provider: WebProvider) -> None:
        """Handle connection timeout when all methods fail."""
        provider.supadata_key = None  # Disable fallback

        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://example.com/article")

            assert "failed" in str(exc_info.value).lower()

    def test_web_dns_failure(self, provider: WebProvider) -> None:
        """Handle DNS resolution failure."""
        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url") as mock_fetch:
            mock_fetch.side_effect = Exception("Name or service not known")

            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://nonexistent.example/page")

            assert exc_info.value is not None

    def test_web_ssl_error(self, provider: WebProvider) -> None:
        """Handle SSL certificate errors."""
        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url") as mock_fetch:
            mock_fetch.side_effect = requests.exceptions.SSLError(
                "SSL certificate verification failed"
            )

            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://invalid-cert.example/page")

            assert exc_info.value is not None

    def test_web_500_server_error(self, provider: WebProvider) -> None:
        """Handle 500 Internal Server Error."""
        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url") as mock_fetch:
            mock_fetch.side_effect = Exception("500 Internal Server Error")

            with pytest.raises(RuntimeError) as exc_info:
                provider.ingest("https://example.com/broken")

            assert exc_info.value is not None

    def test_web_supadata_fallback_also_fails(self, provider: WebProvider) -> None:
        """Handle case when both direct and fallback fail."""
        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None):
            with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
                mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

                with pytest.raises(RuntimeError) as exc_info:
                    provider.ingest("https://blocked.example/page")

                assert "failed" in str(exc_info.value).lower()

    def test_web_raw_content_timeout_fallback(self, provider: WebProvider) -> None:
        """Handle timeout when fetching raw content, falls through to other methods."""
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout("Request timed out")
            # This should try raw, fail, then try trafilatura
            with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None):
                with pytest.raises(RuntimeError):
                    provider.ingest("https://raw.githubusercontent.com/user/repo/main/file.md")


class TestYouTubeProviderNetworkErrors:
    """Test network error handling for YouTube transcript providers."""

    def test_transcript_api_timeout(self) -> None:
        """Handle timeout when fetching YouTube transcript."""
        from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError
        from obsidian_ai_tools.youtube_providers import UnofficialTranscriptProvider

        # Mock the YouTubeTranscriptApi class
        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_class:
            mock_instance = MagicMock()
            mock_instance.fetch.side_effect = Exception("Connection timed out")
            mock_class.return_value = mock_instance

            provider = UnofficialTranscriptProvider()

            with pytest.raises(TranscriptUnavailableError) as exc_info:
                provider.fetch_transcript("test_video_id")

            assert "Connection timed out" in str(exc_info.value)

    def test_transcript_api_network_error(self) -> None:
        """Handle network error when fetching YouTube transcript."""
        from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError
        from obsidian_ai_tools.youtube_providers import UnofficialTranscriptProvider

        with patch("obsidian_ai_tools.youtube_providers.YouTubeTranscriptApi") as mock_class:
            mock_instance = MagicMock()
            mock_instance.fetch.side_effect = Exception("Network is unreachable")
            mock_class.return_value = mock_instance

            provider = UnofficialTranscriptProvider()

            with pytest.raises(TranscriptUnavailableError) as exc_info:
                provider.fetch_transcript("test_video_id")

            assert "Network is unreachable" in str(exc_info.value)

    def test_supadata_transcript_timeout(self) -> None:
        """Handle timeout when fetching transcript via Supadata."""
        from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError
        from obsidian_ai_tools.youtube_providers import SupadataTranscriptProvider

        with patch("obsidian_ai_tools.youtube_providers.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get.side_effect = Exception("Request timed out")
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            provider = SupadataTranscriptProvider(api_key="test_key")

            with pytest.raises(TranscriptUnavailableError) as exc_info:
                provider.fetch_transcript("test_video_id")

            error_msg = str(exc_info.value).lower()
            assert "timed out" in error_msg or "failed" in error_msg

    def test_supadata_transcript_connection_error(self) -> None:
        """Handle connection error when fetching transcript via Supadata."""
        from obsidian_ai_tools.youtube_exceptions import TranscriptUnavailableError
        from obsidian_ai_tools.youtube_providers import SupadataTranscriptProvider

        with patch("obsidian_ai_tools.youtube_providers.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get.side_effect = Exception("Connection refused")
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            provider = SupadataTranscriptProvider(api_key="test_key")

            with pytest.raises(TranscriptUnavailableError) as exc_info:
                provider.fetch_transcript("test_video_id")

            assert (
                "Connection refused" in str(exc_info.value)
                or "failed" in str(exc_info.value).lower()
            )
