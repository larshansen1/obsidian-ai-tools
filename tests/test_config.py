"""Tests for configuration management and environment isolation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_ai_tools.config import Settings, get_settings


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Settings:
    """Provide test settings without relying on .env files.

    This fixture creates a Settings object with explicit values,
    ensuring tests pass identically in CI and local environments
    regardless of .env file presence.
    """
    # Create a temporary vault directory
    vault_path = tmp_path / "test_vault"
    vault_path.mkdir()

    # Create Settings with explicit values
    settings = Settings(
        openrouter_api_key="test-api-key-12345",
        obsidian_vault_path=vault_path,
        obsidian_inbox_folder="inbox",
        llm_model="anthropic/claude-3.5-sonnet",
        max_transcript_length=50000,
        youtube_api_key=None,
        decodo_api_key=None,
        supadata_key=None,
        youtube_transcript_provider_order="direct,supadata,decodo",
        cache_dir=tmp_path / "cache",
        cache_ttl_hours=168,
        circuit_breaker_threshold=3,
        circuit_breaker_timeout_hours=2,
        max_pdf_pages=50,
        max_pdf_size_mb=20,
    )

    return settings


class TestSettingsIsolation:
    """Tests for settings environment isolation."""

    def test_isolated_settings_fixture_works(self, isolated_settings: Settings) -> None:
        """Test that isolated_settings fixture provides valid settings."""
        assert isolated_settings.openrouter_api_key == "test-api-key-12345"
        assert isolated_settings.obsidian_inbox_folder == "inbox"
        assert isolated_settings.llm_model == "anthropic/claude-3.5-sonnet"

    def test_settings_independent_of_env_file(self, tmp_path: Path) -> None:
        """Test that Settings can be created without .env file."""
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        # Create settings with explicit values (no .env file)
        settings = Settings(
            openrouter_api_key="explicit-key",
            obsidian_vault_path=vault_path,
        )

        assert settings.openrouter_api_key == "explicit-key"
        assert settings.obsidian_vault_path == vault_path.resolve()

    def test_settings_with_custom_values(self, tmp_path: Path) -> None:
        """Test Settings accepts all custom values."""
        vault_path = tmp_path / "custom_vault"
        vault_path.mkdir()
        cache_path = tmp_path / "custom_cache"

        settings = Settings(
            openrouter_api_key="custom-key",
            obsidian_vault_path=vault_path,
            obsidian_inbox_folder="custom-inbox",
            llm_model="openai/gpt-4",
            max_transcript_length=100000,
            youtube_api_key="yt-key",
            decodo_api_key="decodo-key",
            supadata_key="supadata-key",
            youtube_transcript_provider_order="decodo,direct",
            cache_dir=cache_path,
            cache_ttl_hours=24,
            circuit_breaker_threshold=5,
            circuit_breaker_timeout_hours=4,
            max_pdf_pages=100,
            max_pdf_size_mb=50,
        )

        assert settings.llm_model == "openai/gpt-4"
        assert settings.max_transcript_length == 100000
        assert settings.youtube_api_key == "yt-key"
        assert settings.cache_ttl_hours == 24
        assert settings.circuit_breaker_threshold == 5
        assert settings.max_pdf_pages == 100


class TestProviderDependencies:
    """Tests for explicit provider dependency mocking."""

    def test_pdf_provider_with_explicit_supadata_key(self, tmp_path: Path) -> None:
        """Test PDFProvider can be created with explicit supadata key."""
        from obsidian_ai_tools.providers.pdf import PDFProvider

        # Mock get_settings to return controlled values
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        mock_settings = Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault_path,
            supadata_key="explicit-supadata-key",
        )

        with patch("obsidian_ai_tools.providers.pdf.get_settings", return_value=mock_settings):
            provider = PDFProvider()
            assert provider.supadata_key == "explicit-supadata-key"

    def test_pdf_provider_without_supadata_key(self, tmp_path: Path) -> None:
        """Test PDFProvider works without supadata key."""
        from obsidian_ai_tools.providers.pdf import PDFProvider

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        mock_settings = Settings(
            openrouter_api_key="test-key",
            obsidian_vault_path=vault_path,
            supadata_key=None,
        )

        with patch("obsidian_ai_tools.providers.pdf.get_settings", return_value=mock_settings):
            provider = PDFProvider()
            assert provider.supadata_key is None


class TestSettingsValidation:
    """Tests for settings validation."""

    def test_vault_path_must_exist(self, tmp_path: Path) -> None:
        """Test that vault path validation fails for non-existent directory."""
        nonexistent = tmp_path / "does_not_exist"

        with pytest.raises(ValueError, match="Vault path does not exist"):
            Settings(
                openrouter_api_key="test-key",
                obsidian_vault_path=nonexistent,
            )

    def test_vault_path_must_be_directory(self, tmp_path: Path) -> None:
        """Test that vault path validation fails for file."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("not a directory")

        with pytest.raises(ValueError, match="not a directory"):
            Settings(
                openrouter_api_key="test-key",
                obsidian_vault_path=file_path,
            )

    def test_invalid_provider_order(self, tmp_path: Path) -> None:
        """Test that invalid provider names are rejected."""
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        with pytest.raises(ValueError, match="Invalid provider name"):
            Settings(
                openrouter_api_key="test-key",
                obsidian_vault_path=vault_path,
                youtube_transcript_provider_order="invalid,direct",
            )

    def test_empty_provider_order(self, tmp_path: Path) -> None:
        """Test that empty provider order is rejected."""
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        with pytest.raises(ValueError, match="Invalid provider name"):
            Settings(
                openrouter_api_key="test-key",
                obsidian_vault_path=vault_path,
                youtube_transcript_provider_order="",
            )


class TestGetSettingsCache:
    """Tests for get_settings caching behavior."""

    def test_get_settings_is_cached(self) -> None:
        """Test that get_settings returns same instance."""
        # Clear the cache first
        get_settings.cache_clear()

        # Skip this test if no .env file exists
        from obsidian_ai_tools.config import find_env_file

        if find_env_file() is None:
            pytest.skip("No .env file found - skipping cache test")

        settings1 = get_settings()
        settings2 = get_settings()

        # Should be the exact same object (cached)
        assert settings1 is settings2

        # Clean up
        get_settings.cache_clear()

    def test_cache_clear_reloads_settings(self) -> None:
        """Test that clearing cache allows reloading settings."""
        from obsidian_ai_tools.config import find_env_file

        if find_env_file() is None:
            pytest.skip("No .env file found - skipping cache test")

        get_settings.cache_clear()
        settings1 = get_settings()

        get_settings.cache_clear()
        settings2 = get_settings()

        # Should be different objects after cache clear
        assert settings1 is not settings2
        # But should have same values
        assert settings1.openrouter_api_key == settings2.openrouter_api_key

        # Clean up
        get_settings.cache_clear()
