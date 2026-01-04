"""Pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def mock_settings_env():
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
MAX PDF_PAGES=50
MAX_PDF_SIZE_MB=20
"""

        env_file.write_text(env_content.strip())

        # Set environment variable to point to temp .env
        original_env = os.environ.copy()
        os.environ["OBSIDIAN_VAULT_PATH"] = "/tmp/test_vault"
        os.environ["OPENROUTER_API_KEY"] = "test_key_for_testing"

        # Set a temporary directory that can be used by tests
        test_vault = Path("/tmp/test_vault")
        test_vault.mkdir(exist_ok=True)
        (test_vault / "inbox").mkdir(exist_ok=True)

        yield

        # Cleanup
        os.environ.clear()
        os.environ.update(original_env)
