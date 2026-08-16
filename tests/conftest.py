"""Pytest configuration and fixtures."""

from collections.abc import Generator, Iterator
from pathlib import Path

import pytest

from obsidian_ai_tools.config import get_settings
from obsidian_ai_tools.observability import ObservabilityDB, _set_db_for_test


@pytest.fixture(autouse=True)
def _obs_db_tmp(tmp_path: Path) -> Generator[None, None, None]:
    """Redirect every get_db() call to an isolated per-test DuckDB.

    Without this, track_command / record_provider_attempt write to the
    real vault database during the test suite, producing bogus counts.
    Tests that need a specific DB call _set_db_for_test() themselves;
    this fixture guarantees a clean slate regardless.
    """
    db = ObservabilityDB(tmp_path / "obs_test.duckdb")
    _set_db_for_test(db)
    yield
    _set_db_for_test(None)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Guarantee no test can read the developer's real .env.

    Two independent layers, both required:

    Layer 1 - ``find_env_file()`` is redirected at the config module, so
    ``get_settings()`` hands a throwaway .env to ``Settings(_env_file=...)``
    instead of whatever real .env sits on the current working directory chain.

    Layer 2 - the same keys are also exported as process environment
    variables. Environment variables outrank dotenv values in
    pydantic-settings' documented precedence, so a direct ``Settings(...)``
    call - which reads no dotenv file at all now - is covered too.

    The cache is cleared on setup *and* teardown so no test inherits another
    test's Settings object.
    """
    base = tmp_path / "_kai_test_env"
    vault = base / "vault"
    (vault / "inbox").mkdir(parents=True)
    cache_dir = base / "cache"

    test_env: dict[str, str] = {
        "OPENROUTER_API_KEY": "test_key_for_testing",
        "OBSIDIAN_VAULT_PATH": str(vault),
        "OBSIDIAN_INBOX_FOLDER": "inbox",
        "LLM_MODEL": "anthropic/claude-3.5-sonnet",
        "MAX_TRANSCRIPT_LENGTH": "50000",
        "YOUTUBE_TRANSCRIPT_PROVIDER_ORDER": "direct,supadata,decodo",
        "CACHE_DIR": str(cache_dir),
        "CACHE_TTL_HOURS": "168",
        "CIRCUIT_BREAKER_THRESHOLD": "3",
        "CIRCUIT_BREAKER_TIMEOUT_HOURS": "2",
        "MAX_PDF_PAGES": "50",
        "MAX_PDF_SIZE_MB": "20",
        # Every remaining secret-bearing field needs a placeholder, otherwise
        # the real .env value for it could still reach Settings.
        "GITHUB_TOKEN": "test-github-token",
        "YOUTUBE_API_KEY": "test-youtube-api-key",
        "DECODO_API_KEY": "test-decodo-api-key",
        "SUPADATA_KEY": "test-supadata-key",
    }

    # Layer 1: the only .env any test can reach.
    env_file = base / ".env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in test_env.items()) + "\n")
    monkeypatch.setattr("obsidian_ai_tools.config.find_env_file", lambda: env_file)

    # Layer 2: environment variables beat dotenv values.
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# External Service Mocking Fixtures


@pytest.fixture
def mock_supadata_response() -> dict[str, str]:
    """Mock successful Supadata API response.

    Provides a standard successful response from Supadata API.
    Can be customized per-test.
    """
    return {
        "content": "Extracted content from Supadata",
        "markdown": "# Extracted Markdown\n\nContent here",
        "title": "Test Article Title",
        "author": "Test Author",
        "date_published": "2026-01-04T12:00:00Z",
    }


@pytest.fixture
def mock_pdf_content(tmp_path: Path) -> bytes:
    """Create mock PDF binary content.

    Returns valid PDF bytes for testing PDF operations.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "Test PDF", "/Author": "Test Author"})

    pdf_path = tmp_path / "test.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)

    return pdf_path.read_bytes()


@pytest.fixture(autouse=True)
def disable_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block real outbound HTTP for every test.

    Autouse, so no test has to opt in. Any unpatched ``requests.get`` or
    ``requests.post`` raises ``RuntimeError("Attempted real network call...")``
    instead of reaching the internet.

    Scope limit, stated plainly: this replaces ``requests.get`` and
    ``requests.post`` and *nothing else*. It does **not** cover ``httpx``
    (which the OpenAI client uses), ``urllib``, or raw sockets. Those still
    have to be patched per test.

    Module-boundary patches keep working. A test doing
    ``patch("obsidian_ai_tools.providers.web.requests.get")`` targets the same
    ``requests`` module object this fixture patched, so the test's mock wins
    for the duration of the patch and the guard is restored afterwards.
    """

    def mock_get(*args: object, **kwargs: object) -> None:
        raise RuntimeError(
            f"Attempted real network call to GET {args[0] if args else 'unknown'}. "
            "Use mocked responses in tests."
        )

    def mock_post(*args: object, **kwargs: object) -> None:
        raise RuntimeError(
            f"Attempted real network call to POST {args[0] if args else 'unknown'}. "
            "Use mocked responses in tests."
        )

    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("requests.post", mock_post)
