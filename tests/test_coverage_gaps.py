"""Tests for coverage gaps and source candidates (issue #136)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vault_compass.coverage_gaps import (
    CandidateSource,
    extract_urls_from_text,
    verify_url_exists,
)
from vault_compass.vault_tools import AiVault, VaultToolError


def test_extract_urls_from_text() -> None:
    """Test URL extraction from text."""
    text = """
    Here's a link: https://arxiv.org/abs/2401.12345
    And another: https://example.com/blog
    Invalid: htp://bad.com
    """

    urls = extract_urls_from_text(text)

    assert "https://arxiv.org/abs/2401.12345" in urls
    assert "https://example.com/blog" in urls
    assert len(urls) == 2


def test_extract_urls_removes_trailing_punctuation() -> None:
    """Test that URL extraction removes trailing punctuation."""
    text = "See https://example.com/page. Here's another: https://other.com/path)"

    urls = extract_urls_from_text(text)

    assert "https://example.com/page" in urls
    assert "https://other.com/path" in urls
    assert not any(url.endswith(".") for url in urls)


def test_verify_url_skips_non_http() -> None:
    """Test that URL verification skips non-HTTP URLs without network calls."""
    assert verify_url_exists("ftp://example.com") is False
    assert verify_url_exists("file:///local/path") is False
    assert verify_url_exists("mailto:user@example.com") is False


def test_candidate_source_immutable() -> None:
    """Test that CandidateSource is immutable."""
    candidate = CandidateSource(
        url="https://example.com",
        title="Example",
        source_type="report",
        from_citations=True,
        note_path="notes/test.md",
    )

    with pytest.raises(AttributeError):
        candidate.url = "https://other.com"  # type: ignore


def test_candidate_source_verified_flag() -> None:
    """Test that verified flag is tracked correctly."""
    verified = CandidateSource(
        url="https://example.com",
        title="Example",
        source_type="report",
        from_citations=True,
        verified=True,
    )

    unverified = CandidateSource(
        url="https://example.com",
        title="Example",
        source_type="report",
        from_citations=True,
        verified=False,
    )

    assert verified.verified is True
    assert unverified.verified is False


def test_vault_tools_coverage_gaps_unknown_topic() -> None:
    """Test that coverage_gaps tool fails with unknown topic."""
    vault = AiVault(
        vault_path=Path("/vault"),
        db_path=Path("/db"),
        definitions=MagicMock(topics={}, ai_exclude_folders=[]),
    )

    with pytest.raises(VaultToolError, match="Unknown topic"):
        vault.coverage_gaps("nonexistent")


def test_vault_tools_source_candidates_unknown_topic() -> None:
    """Test that source_candidates tool fails with unknown topic."""
    vault = AiVault(
        vault_path=Path("/vault"),
        db_path=Path("/db"),
        definitions=MagicMock(topics={}, ai_exclude_folders=[]),
    )

    with pytest.raises(VaultToolError, match="Unknown topic"):
        vault.source_candidates("nonexistent")


def test_vault_tools_coverage_gaps_no_data() -> None:
    """Test that coverage_gaps fails gracefully when database doesn't exist."""
    vault = AiVault(
        vault_path=Path("/vault"),
        db_path=Path("/nonexistent.db"),
        definitions=MagicMock(topics={"research": MagicMock()}),
    )

    with pytest.raises(VaultToolError, match="No topic data yet"):
        vault.coverage_gaps("research")


def test_vault_tools_source_candidates_no_data() -> None:
    """Test that source_candidates fails gracefully when database doesn't exist."""
    vault = AiVault(
        vault_path=Path("/vault"),
        db_path=Path("/nonexistent.db"),
        definitions=MagicMock(topics={"research": MagicMock()}),
    )

    with pytest.raises(VaultToolError, match="No topic data yet"):
        vault.source_candidates("research")
