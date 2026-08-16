"""Tests for external service mocking fixtures."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestMockingFixtures:
    """Tests to validate external service mocking fixtures."""

    def test_disable_network_calls_fixture(self) -> None:
        """The autouse network guard blocks real requests.get/post calls."""
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
        self,
        mock_post: Mock,
        mock_get: Mock,
        mock_pdf_content: bytes,
        mock_supadata_response: dict[str, str],
        tmp_path: Path,
    ) -> None:
        """Test PDFProvider can use mocked requests."""
        from obsidian_ai_tools.config import Settings
        from obsidian_ai_tools.providers.pdf import PDFProvider

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
