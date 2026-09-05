"""Tests for configuration management and environment isolation."""

from pathlib import Path
from unittest.mock import patch

import pytest

# NOTE: find_env_file is imported here, at module level, on purpose. The autouse
# _isolate_settings fixture in conftest.py replaces the *attribute*
# obsidian_ai_tools.config.find_env_file, so a function-body import would hand
# TestFindEnvFile the patched stub instead of the real walk. A module-level
# import binds the original function object at collection time, before any
# fixture runs, which is exactly what those tests need.
from obsidian_ai_tools.config import Settings, find_env_file, get_settings

# The attribute the autouse fixture patches, and the one these tests re-point.
_FIND_ENV_FILE = "obsidian_ai_tools.config.find_env_file"


def _make_vault(tmp_path: Path, name: str = "vault") -> Path:
    """Create a vault directory that passes validate_vault_path."""
    vault = tmp_path / name
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def _write_env_file(directory: Path, **values: str) -> Path:
    """Write a .env file into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    env_path = directory / ".env"
    env_path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    return env_path


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
            github_token="github-token",
            youtube_transcript_provider_order="decodo,direct",
            cache_dir=cache_path,
            circuit_breaker_threshold=5,
            circuit_breaker_timeout_hours=4,
            max_pdf_pages=100,
            max_pdf_size_mb=50,
        )

        assert settings.llm_model == "openai/gpt-4"
        assert settings.max_transcript_length == 100000
        assert settings.youtube_api_key == "yt-key"
        assert settings.github_token == "github-token"
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
    """Tests for get_settings() end to end: caching and the file it reads.

    Every test here points find_env_file() at its own throwaway .env, which
    overrides the redirect the autouse _isolate_settings fixture installs. The
    cache is cleared explicitly before each assertion so no ordering assumption
    is needed; the fixture clears it again on teardown.
    """

    def test_get_settings_is_cached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Repeat calls return the one cached instance."""
        vault = _make_vault(tmp_path)
        env_path = _write_env_file(
            tmp_path / "config",
            OPENROUTER_API_KEY="cache-test-key",
            OBSIDIAN_VAULT_PATH=str(vault),
        )
        monkeypatch.setattr(_FIND_ENV_FILE, lambda: env_path)
        get_settings.cache_clear()

        settings1 = get_settings()
        settings2 = get_settings()

        assert settings1 is settings2
        assert get_settings.cache_info().currsize == 1

    def test_cache_clear_reloads_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cache_clear() yields a distinct object carrying the same values."""
        vault = _make_vault(tmp_path)
        env_path = _write_env_file(
            tmp_path / "config",
            OPENROUTER_API_KEY="cache-test-key",
            OBSIDIAN_VAULT_PATH=str(vault),
        )
        monkeypatch.setattr(_FIND_ENV_FILE, lambda: env_path)

        get_settings.cache_clear()
        settings1 = get_settings()

        get_settings.cache_clear()
        settings2 = get_settings()

        assert settings1 is not settings2
        assert settings1.openrouter_api_key == settings2.openrouter_api_key
        assert settings1.obsidian_vault_path == settings2.obsidian_vault_path

    def test_get_settings_reads_the_file_find_env_file_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test for passing _env_file per call.

        Before that change, Settings read one absolute path frozen at import
        time, so re-pointing find_env_file() changed nothing. Now the values
        must follow the file.
        """
        vault = _make_vault(tmp_path)

        # Environment variables outrank dotenv values in pydantic-settings, and
        # the autouse fixture exports this key. Drop it so the dotenv file is
        # what decides.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        first = _write_env_file(
            tmp_path / "first",
            OPENROUTER_API_KEY="key-from-first-env-file",
            OBSIDIAN_VAULT_PATH=str(vault),
        )
        second = _write_env_file(
            tmp_path / "second",
            OPENROUTER_API_KEY="key-from-second-env-file",
            OBSIDIAN_VAULT_PATH=str(vault),
        )

        monkeypatch.setattr(_FIND_ENV_FILE, lambda: first)
        get_settings.cache_clear()
        assert get_settings().openrouter_api_key == "key-from-first-env-file"

        monkeypatch.setattr(_FIND_ENV_FILE, lambda: second)
        get_settings.cache_clear()
        assert get_settings().openrouter_api_key == "key-from-second-env-file"


class TestGetSettingsErrors:
    """Tests for the two RuntimeError branches in get_settings()."""

    def test_missing_env_file_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No .env anywhere is a pre-flight failure with recovery guidance."""
        monkeypatch.setattr(_FIND_ENV_FILE, lambda: None)
        get_settings.cache_clear()

        with pytest.raises(RuntimeError, match="Could not find .env file") as exc_info:
            get_settings()

        assert "~/.kai/.env" in str(exc_info.value)

    def test_validation_error_names_the_file_that_was_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad .env is wrapped, and the message quotes the file actually read."""
        vault = _make_vault(tmp_path)

        # The autouse fixture exports a valid provider order; the dotenv value
        # only reaches the validator once the environment variable is gone.
        monkeypatch.delenv("YOUTUBE_TRANSCRIPT_PROVIDER_ORDER", raising=False)

        env_path = _write_env_file(
            tmp_path / "bad",
            OPENROUTER_API_KEY="test-key",
            OBSIDIAN_VAULT_PATH=str(vault),
            YOUTUBE_TRANSCRIPT_PROVIDER_ORDER="not-a-provider",
        )
        monkeypatch.setattr(_FIND_ENV_FILE, lambda: env_path)
        get_settings.cache_clear()

        with pytest.raises(RuntimeError, match="Configuration error") as exc_info:
            get_settings()

        message = str(exc_info.value)
        assert str(env_path) in message
        assert "Invalid provider name" in message

    def test_non_validation_error_propagates_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Anything that is not a validation error escapes with its own type."""
        vault = _make_vault(tmp_path)
        env_path = _write_env_file(
            tmp_path / "config",
            OPENROUTER_API_KEY="test-key",
            OBSIDIAN_VAULT_PATH=str(vault),
        )
        monkeypatch.setattr(_FIND_ENV_FILE, lambda: env_path)

        def explode(**kwargs: object) -> Settings:
            raise OSError("dotenv file is unreadable")

        monkeypatch.setattr("obsidian_ai_tools.config.Settings", explode)
        get_settings.cache_clear()

        with pytest.raises(OSError, match="dotenv file is unreadable"):
            get_settings()


class TestFindEnvFile:
    """Tests for the real find_env_file() upward walk and home fallback.

    These call the module-level import at the top of this file, which is the
    original function - the autouse fixture only replaces the attribute on the
    config module.
    """

    def test_finds_env_in_the_current_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The walk starts at the working directory itself."""
        env_path = tmp_path / ".env"
        env_path.write_text("OPENROUTER_API_KEY=test-key\n")
        monkeypatch.chdir(tmp_path)

        assert find_env_file() == env_path

    def test_walks_up_to_a_parent_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .env two levels up is found from a nested working directory."""
        env_path = tmp_path / ".env"
        env_path.write_text("OPENROUTER_API_KEY=test-key\n")
        leaf = tmp_path / "project" / "src"
        leaf.mkdir(parents=True)
        monkeypatch.chdir(leaf)

        assert find_env_file() == env_path

    def test_nearest_env_file_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The first .env encountered on the way up is the one returned."""
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=outer\n")
        inner = tmp_path / "project"
        inner.mkdir()
        inner_env = inner / ".env"
        inner_env.write_text("OPENROUTER_API_KEY=inner\n")
        monkeypatch.chdir(inner)

        assert find_env_file() == inner_env

    def test_falls_back_to_home_kai_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With nothing on the cwd chain, ~/.kai/.env is used."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        home = tmp_path / "home"
        (home / ".kai").mkdir(parents=True)
        home_env = home / ".kai" / ".env"
        home_env.write_text("OPENROUTER_API_KEY=test-key\n")

        monkeypatch.chdir(workdir)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert find_env_file() == home_env

    def test_returns_none_when_nothing_is_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No .env on the chain and no home fallback means None."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        home = tmp_path / "home"
        home.mkdir()

        monkeypatch.chdir(workdir)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert find_env_file() is None
