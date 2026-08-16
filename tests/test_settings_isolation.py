"""Regression guards for test-suite settings hermeticity.

These tests exist so that the leak mechanism described in
docs/test-quality-review.md cannot come back silently:

* the first guards a reintroduced import-time ``env_file`` binding on
  ``Settings``;
* the rest guard the autouse ``_isolate_settings`` fixture being removed or
  weakened.

Hard constraint: nothing here may render a credential value. Assertions use
prefixes, lengths and booleans only, so a failure message is safe to paste
into a log or a pull request.
"""

from pathlib import Path

from obsidian_ai_tools.config import Settings, get_settings

# Prefixes GitHub uses for its token formats. A settings object built inside a
# test must never carry a value starting with any of these.
_REAL_TOKEN_PREFIXES = ("github_pat_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_")

_PLACEHOLDER_MAX_LEN = 64


def test_settings_model_config_has_no_frozen_env_file() -> None:
    """Settings must not resolve a .env path at class-definition time.

    A non-None value here means find_env_file() ran during import and froze an
    absolute path onto the class, which is what made every fixture that tried
    to redirect it inert.
    """
    assert Settings.model_config.get("env_file") is None


def test_get_settings_never_carries_real_credentials(tmp_path: Path) -> None:
    """The cached settings a test sees hold placeholders, not real config."""
    settings = get_settings()

    token = settings.github_token or ""
    assert not token.startswith(_REAL_TOKEN_PREFIXES)
    assert len(token) <= _PLACEHOLDER_MAX_LEN

    assert settings.obsidian_vault_path.is_relative_to(tmp_path)
    assert settings.cache_dir.is_relative_to(tmp_path)


def test_direct_settings_construction_never_reads_the_real_env(tmp_path: Path) -> None:
    """A bare Settings() picks up the fixture's env vars, not a real .env.

    Settings loads no dotenv file of its own any more, so the only values it
    can see are the ones the autouse fixture exported.
    """
    settings = Settings()  # type: ignore[call-arg]

    token = settings.github_token or ""
    assert not token.startswith(_REAL_TOKEN_PREFIXES)
    assert len(token) <= _PLACEHOLDER_MAX_LEN

    assert settings.obsidian_vault_path.is_relative_to(tmp_path)
    for secret in (settings.openrouter_api_key, settings.supadata_key, settings.decodo_api_key):
        assert secret is None or len(secret) <= _PLACEHOLDER_MAX_LEN


def test_settings_cache_is_empty_at_test_start() -> None:
    """No test inherits another test's Settings object."""
    assert get_settings.cache_info().currsize == 0
