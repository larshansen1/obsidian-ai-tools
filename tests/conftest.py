"""Pytest configuration and fixtures."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def mock_settings_env() -> Iterator[None]:
    """Set up test environment variables for all tests.

    Creates a temporary .env file with test configuration to avoid
    requiring actual environment variables in test environment.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # Write minimal test configuration
        env_content = """
OPENROUTER_API_KEY=test_key_for_testing
OBSIDIAN_VAULT_PATH=/tmp/test_vault
OBSIDIAN_INBOX_FOLDER=inbox
LLM_MODEL=anthropic/claude-3.5-sonnet
MAX_TRANSCRIPT_LENGTH=50000
YOUTUBE_TRANSCRIPT_PROVIDER_ORDER=direct,supadata,decodo
CACHE_DIR=/tmp/test_cache
CACHE_TTL_HOURS=168
CIRCUIT_BREAKER_THRESHOLD=3
CIRCUIT_BREAKER_TIMEOUT_HOURS=2
MAX_PDF_PAGES=50
MAX_PDF_SIZE_MB=20
"""

        env_file.write_text(env_content.strip())

        # Set a temporary directory that can be used by tests
        test_vault = Path("/tmp/test_vault")
        test_vault.mkdir(exist_ok=True)
        (test_vault / "inbox").mkdir(exist_ok=True)

        # Change to temp directory where .env exists
        original_cwd = os.getcwd()
        os.chdir(tmpdir)

        yield

        # Cleanup
        os.chdir(original_cwd)


# External Service Mocking Fixtures


@pytest.fixture
def mock_requests_get():
    """Mock requests.get for HTTP GET calls.

    Returns a Mock that can be configured per-test.
    Use with @patch decorator or as a fixture.
    """
    mock = Mock()
    mock.return_value.status_code = 200
    mock.return_value.headers = {"content-type": "text/html"}
    mock.return_value.text = "<html><body>Test content</body></html>"
    mock.return_value.raise_for_status = Mock()
    return mock


@pytest.fixture
def mock_requests_post():
    """Mock requests.post for HTTP POST calls.

    Returns a Mock that can be configured per-test.
    """
    mock = Mock()
    mock.return_value.status_code = 200
    mock.return_value.json.return_value = {"status": "success"}
    mock.return_value.raise_for_status = Mock()
    return mock


@pytest.fixture
def mock_supadata_response():
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
def mock_openrouter_response():
    """Mock successful OpenRouter API response.

    Provides a standard LLM response from OpenRouter.
    """
    return {
        "choices": [
            {
                "message": {
                    "content": '{"title": "Test Note", "summary": "Test summary", "tags": ["test"]}'
                }
            }
        ]
    }


@pytest.fixture
def mock_youtube_transcript():
    """Mock YouTube transcript data.

    Provides sample transcript data as returned by youtube_transcript_api.
    """
    return [
        {"text": "Hello, this is a test video.", "start": 0.0, "duration": 3.5},
        {"text": "This is the second segment.", "start": 3.5, "duration": 2.8},
        {"text": "And this is the final part.", "start": 6.3, "duration": 3.2},
    ]


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


@pytest.fixture(autouse=False)
def disable_network_calls(monkeypatch):
    """Fixture to disable all network calls.

    Use this fixture in tests that should never make real network calls.
    If any code tries to use requests.get/post, it will raise an error.

    Usage:
        def test_something(disable_network_calls):
            # Test code here - network calls will fail
    """

    def mock_get(*args, **kwargs):
        raise RuntimeError(
            f"Attempted real network call to GET {args[0] if args else 'unknown'}. "
            "Use mocked responses in tests."
        )

    def mock_post(*args, **kwargs):
        raise RuntimeError(
            f"Attempted real network call to POST {args[0] if args else 'unknown'}. "
            "Use mocked responses in tests."
        )

    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("requests.post", mock_post)
