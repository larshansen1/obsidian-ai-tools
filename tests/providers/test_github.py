"""Tests for GitHub repository documentation ingestion."""

import base64
from typing import Any
from unittest.mock import patch

import pytest

from obsidian_ai_tools.providers.factory import ProviderFactory
from obsidian_ai_tools.providers.github import GitHubProvider, GitHubRepositoryError
from obsidian_ai_tools.providers.web import WebProvider


class FakeResponse:
    """Small response double for GitHub API tests."""

    def __init__(
        self,
        payload: Any,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} error")


def encoded_file(content: str) -> dict[str, str]:
    """Return a GitHub contents API file payload."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return {"type": "file", "encoding": "base64", "content": encoded}


def test_github_provider_validates_repo_urls_without_stealing_blob_urls() -> None:
    """Repo URLs use GitHubProvider, while blob URLs stay with WebProvider."""
    provider = GitHubProvider()

    assert provider.validate("https://github.com/user/repo")
    assert provider.validate("https://github.com/user/repo/tree/main/docs")
    assert not provider.validate("https://github.com/user/repo/blob/main/README.md")

    assert isinstance(ProviderFactory.get_provider("https://github.com/user/repo"), GitHubProvider)
    blob_provider = ProviderFactory.get_provider("https://github.com/user/repo/blob/main/README.md")
    assert isinstance(blob_provider, WebProvider)


def test_github_provider_ingests_bounded_documentation_with_provenance() -> None:
    """A repo with README and docs is aggregated from selected documentation only."""
    provider = GitHubProvider()
    responses = {
        "https://api.github.com/repos/user/repo": FakeResponse({"default_branch": "main"}),
        "https://api.github.com/repos/user/repo/contents": FakeResponse(
            [
                {"type": "file", "name": "README.md", "path": "README.md", "size": 100},
                {"type": "file", "name": "pyproject.toml", "path": "pyproject.toml", "size": 50},
                {"type": "file", "name": "main.py", "path": "src/main.py", "size": 10},
                {"type": "dir", "name": "docs", "path": "docs"},
            ]
        ),
        "https://api.github.com/repos/user/repo/contents/docs": FakeResponse(
            [
                {"type": "file", "name": "principles.md", "path": "docs/principles.md"},
                {"type": "file", "name": "logo.png", "path": "docs/logo.png"},
            ]
        ),
        "https://api.github.com/repos/user/repo/contents/README.md": FakeResponse(
            encoded_file("# Repo\n\nPurpose text.")
        ),
        "https://api.github.com/repos/user/repo/contents/docs/principles.md": FakeResponse(
            encoded_file("# Principles\n\nDocumentation-first source selection.")
        ),
        "https://api.github.com/repos/user/repo/contents/pyproject.toml": FakeResponse(
            encoded_file("[project]\nname = 'repo'")
        ),
    }
    fetched_urls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        fetched_urls.append(url)
        return responses[url]

    with patch("obsidian_ai_tools.providers.github.requests.get", side_effect=fake_get):
        result = provider._ingest("https://github.com/user/repo")

    assert result.source_type == "github"
    assert result.url == "https://github.com/user/repo"
    assert "README.md" in result.content
    assert "docs/principles.md" in result.content
    assert "pyproject.toml" in result.content
    assert "src/main.py" not in result.content
    assert "docs/logo.png" not in result.content
    assert result.source_references == [
        "[README.md](https://github.com/user/repo/blob/main/README.md)",
        "[docs/principles.md](https://github.com/user/repo/blob/main/docs/principles.md)",
        "[pyproject.toml](https://github.com/user/repo/blob/main/pyproject.toml)",
    ]
    assert "https://api.github.com/repos/user/repo/contents/src/main.py" not in fetched_urls


def test_github_provider_treats_tree_docs_url_as_documentation_root() -> None:
    """A /tree/<ref>/docs URL should select docs files from that directory."""
    provider = GitHubProvider()
    responses = {
        "https://api.github.com/repos/user/repo/contents/docs": FakeResponse(
            [{"type": "file", "name": "usage.md", "path": "docs/usage.md"}]
        ),
        "https://api.github.com/repos/user/repo/contents/docs/usage.md": FakeResponse(
            encoded_file("# Usage\n\nRun the CLI.")
        ),
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        return responses[url]

    with patch("obsidian_ai_tools.providers.github.requests.get", side_effect=fake_get):
        result = provider._ingest("https://github.com/user/repo/tree/main/docs")

    assert result.url == "https://github.com/user/repo/tree/main"
    assert "docs/usage.md" in result.content
    assert result.source_references == [
        "[docs/usage.md](https://github.com/user/repo/blob/main/docs/usage.md)"
    ]


def test_github_provider_reports_private_repo_without_token() -> None:
    """Private repos produce an actionable credentials error."""
    provider = GitHubProvider()
    response = FakeResponse(
        {"message": "Requires authentication"},
        status_code=403,
        headers={"X-RateLimit-Remaining": "42"},
    )

    with patch("obsidian_ai_tools.providers.github.requests.get", return_value=response):
        with pytest.raises(GitHubRepositoryError, match="GITHUB_TOKEN"):
            provider._ingest("https://github.com/user/private")


def test_github_provider_reports_rate_limit() -> None:
    """Unauthenticated rate limits point users toward a token."""
    provider = GitHubProvider()
    response = FakeResponse(
        {"message": "API rate limit exceeded"},
        status_code=403,
        headers={"X-RateLimit-Remaining": "0"},
    )

    with patch("obsidian_ai_tools.providers.github.requests.get", return_value=response):
        with pytest.raises(GitHubRepositoryError, match="rate limit exceeded"):
            provider._ingest("https://github.com/user/repo")


def test_github_provider_reports_insufficient_documentation() -> None:
    """Repositories without docs fail before calling the LLM."""
    provider = GitHubProvider()
    responses = {
        "https://api.github.com/repos/user/repo": FakeResponse({"default_branch": "main"}),
        "https://api.github.com/repos/user/repo/contents": FakeResponse(
            [{"type": "file", "name": "main.py", "path": "main.py"}]
        ),
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        return responses[url]

    with patch("obsidian_ai_tools.providers.github.requests.get", side_effect=fake_get):
        with pytest.raises(GitHubRepositoryError, match="No documentation files"):
            provider._ingest("https://github.com/user/repo")


def test_github_provider_uses_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub requests include Authorization when GITHUB_TOKEN is configured."""
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    provider = GitHubProvider()

    with patch("obsidian_ai_tools.providers.github.requests.get") as mock_get:
        mock_get.return_value = FakeResponse({"default_branch": "main"})
        provider._fetch_default_branch(provider_ref())

    headers = mock_get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer secret-token"


def provider_ref() -> Any:
    """Build a parsed repo ref without exposing parse details to this test."""
    from obsidian_ai_tools.providers.github import parse_github_repo_url

    ref = parse_github_repo_url("https://github.com/user/repo")
    assert ref is not None
    return ref
