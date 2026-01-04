"""Pytest configuration and fixtures."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def mock_settings_env() -> Iterator[None]:
    """Set up test environment variables for all tests.

    Sets environment variables directly to avoid requiring actual
    environment variables or .env files in test environment.
    """
    original_env = os.environ.copy()

    # Set minimal test configuration
    os.environ["OPENROUTER_API_KEY"] = "test_key_for_testing"
    os.environ["OBSIDIAN_VAULT_PATH"] = "/tmp/test_vault"
    os.environ["OBSIDIAN_INBOX_FOLDER"] = "inbox"
    os.environ["LLM_MODEL"] = "anthropic/claude-3.5-sonnet"
    os.environ["MAX_TRANSCRIPT_LENGTH"] = "50000"
    os.environ["YOUTUBE_TRANSCRIPT_PROVIDER_ORDER"] = "direct,supadata,decodo"
    os.environ["CACHE_DIR"] = "/tmp/test_cache"
    os.environ["CACHE_TTL_HOURS"] = "168"
    os.environ["CIRCUIT_BREAKER_THRESHOLD"] = "3"
    os.environ["CIRCUIT_BREAKER_TIMEOUT_HOURS"] = "2"
    os.environ["MAX_PDF_PAGES"] = "50"
    os.environ["MAX_PDF_SIZE_MB"] = "20"

    # Set a temporary directory that can be used by tests
    test_vault = Path("/tmp/test_vault")
    test_vault.mkdir(exist_ok=True)
    (test_vault / "inbox").mkdir(exist_ok=True)

    yield

    # Cleanup
    os.environ.clear()
    os.environ.update(original_env)
