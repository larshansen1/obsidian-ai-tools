"""Tests for external service mocking fixtures."""

import pytest
from unittest.mock import patch


class TestMockingFixtures:
    """Tests to validate external service mocking fixtures."""

    def test_mock_requests_get_fixture(self, mock_requests_get):
        """Test that mock_requests_get fixture works."""
        assert mock_requests_get is not None
        assert mock_requests_get.return_value.status_code == 200
        assert "content-type" in mock_requests_get.return_value.headers

    def test_mock_requests_post_fixture(self, mock_requests_post):
        """Test that mock_requests_post fixture works."""
        assert mock_requests_post is not None
        assert mock_requests_post.return_value.status_code == 200
        result = mock_requests_post.return_value.json()
        assert result["status"] == "success"

    def test_mock_supadata_response_fixture(self, mock_supadata_response):
        """Test that mock_supadata_response fixture provides valid data."""
        assert "content" in mock_supadata_response
        assert "title" in mock_supadata_response
        assert mock_supadata_response["title"] == "Test Article Title"

    def test_mock_openrouter_response_fixture(self, mock_openrouter_response):
        """Test that mock_openrouter_response fixture provides valid LLM response."""
        assert "choices" in mock_openrouter_response
        assert len(mock_openrouter_response["choices"]) > 0
        message = mock_openrouter_response["choices"][0]["message"]
        assert "content" in message

    def test_mock_youtube_transcript_fixture(self, mock_youtube_transcript):
        """Test that mock_youtube_transcript fixture provides valid data."""
        assert isinstance(mock_youtube_transcript, list)
        assert len(mock_youtube_transcript) > 0
        assert "text" in mock_youtube_transcript[0]
        assert "start" in mock_youtube_transcript[0]

    def test_mock_pdf_content_fixture(self, mock_pdf_content):
        """Test that mock_pdf_content fixture creates valid PDF bytes."""
        assert isinstance(mock_pdf_content, bytes)
        # PDF files start with %PDF
        assert mock_pdf_content.startswith(b"%PDF")

    def test_disable_network_calls_fixture(self, disable_network_calls):
        """Test that disable_network_calls prevents real network requests."""
        import requests

        # Attempting to make real network call should raise error
        with pytest.raises(RuntimeError, match="Attempted real network call"):
            requests.get("https://example.com")

        with pytest.raises(RuntimeError, match="Attempted real network call"):
            requests.post("https://example.com")


class TestFixtureIntegrationWithProviders:
    """Test that fixtures integrate well with provider code."""

    @patch("obsidian_ai_tools.providers.pdf.requests.get")
    @patch("obsidian_ai_tools.providers.pdf.requests.post")
    def test_pdf_provider_uses_mocked_requests(
        self, mock_post, mock_get, mock_pdf_content, mock_supadata_response, tmp_path
    ):
        """Test PDFProvider can use mocked requests."""
        from obsidian_ai_tools.providers.pdf import PDFProvider
        from obsidian_ai_tools.config import Settings

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        # Configure mocks
        mock_get.return_value.headers = {
            "content-type": "application/pdf",
            "content-length": str(len(mock_pdf_content)),
        }
        mock_get.return_value.iter_content = lambda chunk_size: [mock_pdf_content]
        mock_get.return_value.raise_for_status = lambda: None

        # Mock Supadata fallback
        mock_post.return_value.json.return_value = mock_supadata_response
        mock_post.return_value.raise_for_status = lambda: None

        # Create provider with mocked settings
        settings = Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault_path,
            supadata_key="test-supadata-key",
        )

        with patch("obsidian_ai_tools.providers.pdf.get_settings", return_value=settings):
            provider = PDFProvider()
            assert provider.supadata_key == "test-supadata-key"

            # This would normally make real network calls, but we've mocked them
            # The test demonstrates the mocking infrastructure works

    def test_fixture_customization_per_test(self, mock_requests_get):
        """Test that fixtures can be customized per-test."""
        # Customize the mock for this specific test
        mock_requests_get.return_value.status_code = 404
        mock_requests_get.return_value.headers = {"content-type": "application/json"}

        assert mock_requests_get.return_value.status_code == 404
        assert mock_requests_get.return_value.headers["content-type"] == "application/json"
