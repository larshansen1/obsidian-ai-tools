"""Tests for GitHub repository documentation ingestion."""

import base64
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import requests

from obsidian_ai_tools.config import get_settings
from obsidian_ai_tools.providers.factory import ProviderFactory
from obsidian_ai_tools.providers.github import (
    DocumentationCandidate,
    DocumentationFile,
    GitHubProvider,
    GitHubRepositoryError,
    parse_github_repo_url,
)
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
            raise requests.HTTPError(f"{self.status_code} Server Error")


class BrokenJsonResponse(FakeResponse):
    """Response double whose body cannot be parsed as JSON."""

    def json(self) -> Any:
        raise ValueError("invalid JSON")


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


def test_github_provider_prioritizes_canonical_readme_before_translations() -> None:
    """The canonical README should be selected before localized README variants."""
    provider = GitHubProvider()
    responses = {
        "https://api.github.com/repos/user/repo": FakeResponse({"default_branch": "main"}),
        "https://api.github.com/repos/user/repo/contents": FakeResponse(
            [
                {"type": "file", "name": "README.zh-CN.md", "path": "README.zh-CN.md"},
                {"type": "file", "name": "README.md", "path": "README.md"},
                {"type": "file", "name": "README.ja-JP.md", "path": "README.ja-JP.md"},
            ]
        ),
        "https://api.github.com/repos/user/repo/contents/README.md": FakeResponse(
            encoded_file("# Canonical README")
        ),
        "https://api.github.com/repos/user/repo/contents/README.ja-JP.md": FakeResponse(
            encoded_file("# Japanese README")
        ),
        "https://api.github.com/repos/user/repo/contents/README.zh-CN.md": FakeResponse(
            encoded_file("# Chinese README")
        ),
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        return responses[url]

    with patch("obsidian_ai_tools.providers.github.requests.get", side_effect=fake_get):
        result = provider._ingest("https://github.com/user/repo")

    assert result.source_references[0] == (
        "[README.md](https://github.com/user/repo/blob/main/README.md)"
    )


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
    # No get_settings patch needed: environment variables outrank dotenv
    # values, so the rebuilt Settings carry this placeholder and _load_token()
    # returns it through its normal path.
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    get_settings.cache_clear()

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


# ---------------------------------------------------------------------------
# parse_github_repo_url
# ---------------------------------------------------------------------------


def test_parse_github_repo_url_scheme_and_host_rules() -> None:
    """Only http(s) github.com URLs parse; scheme and host are both required."""
    assert parse_github_repo_url("ftp://github.com/user/repo") is None
    assert parse_github_repo_url("https://gitlab.com/user/repo") is None
    assert parse_github_repo_url("https://github.com/user") is None

    http_ref = parse_github_repo_url("http://github.com/user/repo")
    assert http_ref is not None
    assert http_ref.owner == "user"
    assert http_ref.repo == "repo"
    assert http_ref.repository_url == "https://github.com/user/repo"

    upper_host = parse_github_repo_url("https://GITHUB.COM/user/repo")
    assert upper_host is not None
    assert upper_host.display_name == "user/repo"


def test_parse_github_repo_url_repo_name_edges() -> None:
    """.git suffixes are stripped and degenerate repo names are rejected."""
    git_ref = parse_github_repo_url("https://github.com/user/repo.git")
    assert git_ref is not None
    assert git_ref.repo == "repo"

    # A repo segment of exactly ".git" strips to an empty name.
    assert parse_github_repo_url("https://github.com/user/.git") is None

    # Path stripping removes only "/" characters, never other whitespace.
    space_ref = parse_github_repo_url("https://github.com/user/repo ?tab=readme")
    assert space_ref is not None
    assert space_ref.repo == "repo "

    x_ref = parse_github_repo_url("https://github.com/Xuser/repo")
    assert x_ref is not None
    assert x_ref.owner == "Xuser"


def test_parse_github_repo_url_tree_routes() -> None:
    """/tree/<ref> URLs carry the ref and an optional docs prefix."""
    shallow = parse_github_repo_url("https://github.com/user/repo/tree/main")
    assert shallow is not None
    assert shallow.ref == "main"
    assert shallow.docs_prefix is None

    deep = parse_github_repo_url("https://github.com/user/repo/tree/main/docs/api")
    assert deep is not None
    assert deep.ref == "main"
    assert deep.docs_prefix == "docs/api"

    assert parse_github_repo_url("https://github.com/user/repo/tree") is None


# ---------------------------------------------------------------------------
# _priority_for_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", 0),
        ("readme", 0),
        ("readme.markdown", 0),
        ("docs/README.md", 20),
        ("docs/sub/README.md", 20),
        ("readme.zh-CN.md", 5),
        ("readme notes.md", 5),
        ("docs/readme.zh-CN.md", 25),
        ("CONTRIBUTING.md", 10),
        ("docs/contributing.md", 10),
        ("security.md", 11),
        ("docs/SECURITY.md", 11),
        ("changelog.md", 12),
        ("CHANGELOG.old.md", 12),
        ("docs/guide.md", 30),
        ("doc/guide.md", 30),
        ("documentation/guide.md", 30),
        ("docs/package.json", 30),
        ("package.json", 70),
        ("go.mod", 70),
        ("src/package.json", 70),
        ("notes.md", 90),
        ("src/guide.md", 90),
    ],
)
def test_priority_for_path_exact_values(path: str, expected: int) -> None:
    """Every priority tier maps to its exact documented value."""
    assert GitHubProvider()._priority_for_path(path) == expected


# ---------------------------------------------------------------------------
# _github_error_message
# ---------------------------------------------------------------------------


def rate_limit_message() -> str:
    return (
        "GitHub API rate limit exceeded while fetching repository documentation. "
        "Set GITHUB_TOKEN to use authenticated GitHub API access."
    )


def access_denied_message(detail: str) -> str:
    return (
        "GitHub repository requires access or credentials. "
        "Set GITHUB_TOKEN with repository read access and try again. "
        f"GitHub response: {detail}"
    )


def test_github_error_message_uses_payload_message_when_not_rate_limited() -> None:
    """A denied response surfaces the GitHub message verbatim."""
    response = FakeResponse(
        {"message": "Bad credentials"},
        headers={"X-RateLimit-Remaining": "42"},
    )
    assert GitHubProvider()._github_error_message(response) == access_denied_message(
        "Bad credentials"
    )


def test_github_error_message_reports_rate_limit_from_header() -> None:
    """A zero remaining quota is a rate limit even without message text."""
    response = FakeResponse({}, headers={"X-RateLimit-Remaining": "0"})
    assert GitHubProvider()._github_error_message(response) == rate_limit_message()


def test_github_error_message_reports_rate_limit_from_message_text() -> None:
    """The rate-limit branch also triggers on the message alone (case-insensitive)."""
    response = FakeResponse({"message": "Rate Limit exceeded"})
    assert GitHubProvider()._github_error_message(response) == rate_limit_message()


def test_github_error_message_falls_back_to_response_text() -> None:
    """Without a payload message, the raw response text is quoted."""
    response = FakeResponse({}, text="raw failure body")
    assert GitHubProvider()._github_error_message(response) == access_denied_message(
        "raw failure body"
    )


def test_github_error_message_falls_back_to_default_denial() -> None:
    """No JSON and no text yields the default denial detail."""
    response = BrokenJsonResponse(None, text="")
    assert GitHubProvider()._github_error_message(response) == access_denied_message(
        "GitHub access denied"
    )


def test_github_error_message_prefers_text_over_default_when_json_broken() -> None:
    """Broken JSON still allows the raw text to be surfaced."""
    response = BrokenJsonResponse(None, text="fallback body")
    assert GitHubProvider()._github_error_message(response) == access_denied_message(
        "fallback body"
    )


# ---------------------------------------------------------------------------
# _github_get
# ---------------------------------------------------------------------------


def test_github_get_raises_actionable_error_for_denied_status() -> None:
    """401 responses go through the error-message builder."""
    provider = GitHubProvider()
    response = FakeResponse(
        {"message": "Bad credentials"},
        status_code=401,
        headers={"X-RateLimit-Remaining": "42"},
    )
    with patch("obsidian_ai_tools.providers.github.requests.get", return_value=response):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._github_get("https://api.github.com/repos/user/repo")
    assert str(excinfo.value) == access_denied_message("Bad credentials")


def test_github_get_reports_missing_repository_exactly() -> None:
    """404 responses produce the exact private-repo guidance message."""
    provider = GitHubProvider()
    response = FakeResponse({"message": "Not Found"}, status_code=404)
    expected = (
        "GitHub repository or documentation path was not found. "
        "For private repositories, set GITHUB_TOKEN with repository read access."
    )
    with patch("obsidian_ai_tools.providers.github.requests.get", return_value=response):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._github_get("https://api.github.com/repos/user/repo")
    assert str(excinfo.value) == expected


def test_github_get_wraps_other_http_errors() -> None:
    """Unexpected HTTP errors are wrapped in GitHubRepositoryError."""
    provider = GitHubProvider()
    response = FakeResponse({"message": "boom"}, status_code=500)
    with patch("obsidian_ai_tools.providers.github.requests.get", return_value=response):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._github_get("https://api.github.com/repos/user/repo")
    assert str(excinfo.value) == "GitHub request failed: 500 Server Error"


def test_github_get_passes_params_and_timeout() -> None:
    """Requests carry params, a 30s timeout, and the provider headers."""
    provider = GitHubProvider()
    with patch("obsidian_ai_tools.providers.github.requests.get") as mock_get:
        mock_get.return_value = FakeResponse({"default_branch": "main"})
        data = provider._github_get("https://api.github.com/x", params={"ref": "main"})
    assert data == {"default_branch": "main"}
    kwargs = mock_get.call_args.kwargs
    assert kwargs["params"] == {"ref": "main"}
    assert kwargs["timeout"] == 30
    assert kwargs["headers"] == provider._headers()


# ---------------------------------------------------------------------------
# _headers / _load_token
# ---------------------------------------------------------------------------


def test_headers_without_and_with_token() -> None:
    """Headers are exactly the GitHub JSON accept/version pair, plus auth."""
    provider = GitHubProvider()
    provider.github_token = None
    assert provider._headers() == {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    provider.github_token = "secret"
    assert provider._headers() == {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer secret",
    }


def clear_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


def test_load_token_prefers_settings_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A settings token wins over environment variables."""
    clear_token_env(monkeypatch)
    settings = SimpleNamespace(github_token="settings-token")
    with patch("obsidian_ai_tools.providers.github.get_settings", return_value=settings):
        assert GitHubProvider()._load_token() == "settings-token"


def test_load_token_logs_debug_when_settings_fail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Settings failures fall back to env and log a debug record with traceback."""
    clear_token_env(monkeypatch)
    with patch(
        "obsidian_ai_tools.providers.github.get_settings",
        side_effect=RuntimeError("settings unavailable"),
    ):
        with caplog.at_level(logging.DEBUG, logger="obsidian_ai_tools.providers.github"):
            provider = GitHubProvider()
    assert provider.github_token is None
    record = caplog.records[0]
    assert record.message == "GitHub token unavailable from settings"
    assert record.exc_info is not None
    # exc_info must carry the real traceback tuple, not merely be non-None:
    # exc_info=False on the log call yields record.exc_info == False.
    assert record.exc_info[0] is RuntimeError


def test_load_token_reads_env_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """GITHUB_TOKEN is preferred, then GH_TOKEN."""
    clear_token_env(monkeypatch)
    settings = SimpleNamespace(github_token="")
    with patch("obsidian_ai_tools.providers.github.get_settings", return_value=settings):
        monkeypatch.setenv("GITHUB_TOKEN", "gtoken")
        assert GitHubProvider()._load_token() == "gtoken"
        monkeypatch.delenv("GITHUB_TOKEN")
        monkeypatch.setenv("GH_TOKEN", "gh-token")
        assert GitHubProvider()._load_token() == "gh-token"


# ---------------------------------------------------------------------------
# _fetch_default_branch / _list_directory
# ---------------------------------------------------------------------------


def test_fetch_default_branch_returns_branch_name() -> None:
    provider = GitHubProvider()
    response = FakeResponse({"default_branch": "develop"})
    with patch("obsidian_ai_tools.providers.github.requests.get", return_value=response):
        assert provider._fetch_default_branch(provider_ref()) == "develop"


@pytest.mark.parametrize("payload", [{"default_branch": 123}, {"default_branch": ""}, {}])
def test_fetch_default_branch_rejects_invalid_payload(payload: dict[str, Any]) -> None:
    provider = GitHubProvider()
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(payload),
    ):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._fetch_default_branch(provider_ref())
    assert str(excinfo.value) == "Could not determine default branch for user/repo."


def test_list_directory_wraps_single_object_response() -> None:
    """A single-object contents response is normalized to a one-item list."""
    provider = GitHubProvider()
    entry = {"type": "dir", "name": "docs", "path": "docs"}
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(entry),
    ) as mock_get:
        assert provider._list_directory(provider_ref(), "main", None) == [entry]
    assert mock_get.call_args.args == ("https://api.github.com/repos/user/repo/contents",)
    assert mock_get.call_args.kwargs["params"] == {"ref": "main"}


def test_list_directory_appends_path_to_contents_url() -> None:
    provider = GitHubProvider()
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse([]),
    ) as mock_get:
        assert provider._list_directory(provider_ref(), "main", "docs") == []
    assert mock_get.call_args.args == ("https://api.github.com/repos/user/repo/contents/docs",)
    assert mock_get.call_args.kwargs["params"] == {"ref": "main"}


def test_list_directory_rejects_unexpected_payload_and_truncates() -> None:
    provider = GitHubProvider()
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse("unexpected"),
    ):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._list_directory(provider_ref(), "main", None)
    assert str(excinfo.value) == "Unexpected GitHub contents response for user/repo."

    entries = [{"type": "file", "path": f"f{i}.md"} for i in range(3)]
    provider.max_directory_entries = 2
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(entries),
    ):
        assert provider._list_directory(provider_ref(), "main", None) == entries[:2]


# ---------------------------------------------------------------------------
# _discover_documentation_candidates / _collect_docs_directory
# ---------------------------------------------------------------------------


def test_discover_candidates_root_rules() -> None:
    """Root selection: doc files at root only, docs dirs descended, others skipped."""
    provider = GitHubProvider()
    ref = provider_ref()
    root = [
        {"type": "file", "name": "README.md", "path": "README.md", "size": 100},
        {"type": "file", "name": "notes.md", "path": "notes.md", "size": 10},
        {"type": "file", "name": "readme.md"},
        {"type": "dir", "name": "src", "path": "src"},
        {"type": "dir", "name": "docs", "path": "docs"},
        {"type": "symlink", "name": "documentation", "path": "documentation"},
    ]
    listings: dict[str | None, list[dict[str, Any]]] = {
        None: root,
        "docs": [{"type": "file", "name": "a.md", "path": "docs/a.md", "size": 5}],
    }
    calls: list[tuple[Any, str, str | None]] = []

    def fake_list(repo: Any, list_ref: str, path: str | None) -> list[dict[str, Any]]:
        calls.append((repo, list_ref, path))
        return listings[path]

    with patch.object(provider, "_list_directory", side_effect=fake_list):
        candidates = provider._discover_documentation_candidates(ref, "main")

    assert [(c.path, c.priority, c.size) for c in candidates] == [
        ("README.md", 0, 100),
        ("", 90, None),
        ("docs/a.md", 30, 5),
    ]
    assert [call[1:] for call in calls] == [("main", None), ("main", "docs")]
    assert all(call[0] is ref for call in calls)


def test_discover_candidates_respects_docs_depth_limit() -> None:
    """Docs trees are collected to exactly max_docs_depth levels."""
    provider = GitHubProvider()
    source = "https://github.com/user/repo/tree/main/docs"
    ref = parse_github_repo_url(source)
    assert ref is not None
    listings: dict[str | None, list[dict[str, Any]]] = {
        "docs": [
            {"type": "file", "name": "intro.md", "path": "docs/intro.md", "size": 3},
            {"type": "file", "name": "logo.png", "path": "docs/logo.png", "size": 9},
            {"type": "dir", "name": "guide", "path": "docs/guide"},
        ],
        "docs/guide": [
            {"type": "file", "name": "b.md", "path": "docs/guide/b.md", "size": 2},
            {"type": "file", "name": "z.md", "size": 7},
            {"type": "dir", "name": "deep", "path": "docs/guide/deep"},
        ],
        "docs/guide/deep": [
            {
                "type": "file",
                "name": "c.md",
                "path": "docs/guide/deep/c.md",
                "size": 2,
            },
            {"type": "dir", "name": "abyss", "path": "docs/guide/deep/abyss"},
        ],
        "docs/guide/deep/abyss": [
            {
                "type": "file",
                "name": "d.md",
                "path": "docs/guide/deep/abyss/d.md",
                "size": 2,
            },
            {"type": "dir", "name": "void", "path": "docs/guide/deep/abyss/void"},
        ],
        "docs/guide/deep/abyss/void": [
            {
                "type": "file",
                "name": "e.md",
                "path": "docs/guide/deep/abyss/void/e.md",
            },
        ],
    }
    listed: list[str | None] = []

    def fake_list(repo: Any, list_ref: str, path: str | None) -> list[dict[str, Any]]:
        assert repo.owner == "user"
        assert list_ref == "main"
        listed.append(path)
        return listings[path]

    with patch.object(provider, "_list_directory", side_effect=fake_list):
        candidates = provider._discover_documentation_candidates(ref, "main")

    assert [(c.path, c.size) for c in candidates] == [
        ("docs/intro.md", 3),
        ("docs/guide/b.md", 2),
        ("", 7),
        ("docs/guide/deep/c.md", 2),
        ("docs/guide/deep/abyss/d.md", 2),
    ]
    assert listed == ["docs", "docs/guide", "docs/guide/deep", "docs/guide/deep/abyss"]


def test_discover_candidates_docs_prefix_sets_priorities_and_filters() -> None:
    """Docs-prefix roots score files with real priorities and skip non-files."""
    provider = GitHubProvider()
    source = "https://github.com/user/repo/tree/main/docs"
    ref = parse_github_repo_url(source)
    assert ref is not None
    listings: dict[str | None, list[dict[str, Any]]] = {
        "docs": [
            {"type": "file", "name": "guide.md", "path": "docs/guide.md", "size": 12},
            {"type": "file", "name": "notes.txt", "path": "docs/notes.txt", "size": 3},
            {"type": "symlink", "name": "alias.md", "path": "docs/alias.md", "size": 1},
            {"type": "dir", "name": "guide", "path": "docs/guide"},
        ],
        "docs/guide": [
            {"type": "file", "name": "deep.md", "path": "docs/guide/deep.md", "size": 4},
        ],
    }
    listed: list[str | None] = []

    def fake_list(repo: Any, list_ref: str, path: str | None) -> list[dict[str, Any]]:
        assert repo.owner == "user"
        assert list_ref == "main"
        listed.append(path)
        return listings[path]

    with patch.object(provider, "_list_directory", side_effect=fake_list):
        candidates = provider._discover_documentation_candidates(ref, "main")

    assert [(c.path, c.priority, c.size) for c in candidates] == [
        ("docs/guide.md", 30, 12),
        ("docs/notes.txt", 30, 3),
        ("docs/guide/deep.md", 30, 4),
    ]
    assert listed == ["docs", "docs/guide"]


# ---------------------------------------------------------------------------
# _is_root_documentation_file / _is_docs_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("readme.md", True),
        ("README.markdown", True),
        ("contributing.rst", True),
        ("SECURITY.adoc", True),
        ("changelog.mdx", True),
        ("code_of_conduct.md", True),
        ("license.txt", True),
        ("readme.old.md", True),
        ("package.json", True),
        ("cargo.toml", True),
        ("setup.py", True),
        ("readme", False),
        ("notes.md", False),
        ("contributing.pdf", False),
        ("logo.png", False),
        ("myreadme.md", False),
    ],
)
def test_is_root_documentation_file(name: str, expected: bool) -> None:
    """Root files qualify via metadata name or prefix plus a doc extension."""
    assert GitHubProvider()._is_root_documentation_file(name) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.md", True),
        ("B.TXT", True),
        ("c.markdown", True),
        ("d.mdx", True),
        ("e.py", False),
        ("README", False),
        ("logo.png", False),
    ],
)
def test_is_docs_file(name: str, expected: bool) -> None:
    """Docs directories accept any file with a documentation extension."""
    assert GitHubProvider()._is_docs_file(name) is expected


# ---------------------------------------------------------------------------
# _fetch_selected_files / _fetch_file_text
# ---------------------------------------------------------------------------


def test_fetch_selected_files_enforces_character_budget() -> None:
    """The total budget truncates later files and stops further fetches."""
    provider = GitHubProvider()
    provider.max_total_chars = 5
    provider.max_file_chars = 40
    candidates = [
        DocumentationCandidate(path="a.md", priority=0, size=4),
        DocumentationCandidate(path="blank.md", priority=1, size=3),
        DocumentationCandidate(path="b.md", priority=2, size=3),
        DocumentationCandidate(path="c.md", priority=3, size=3),
    ]
    contents = {"a.md": "AAAA", "blank.md": "   ", "b.md": "BBB", "c.md": "CCC"}
    calls: list[tuple[str, str]] = []

    def fake_fetch(repo: Any, fetch_ref: str, path: str) -> str:
        assert repo.owner == "user"
        calls.append((fetch_ref, path))
        return contents[path]

    with patch.object(provider, "_fetch_file_text", side_effect=fake_fetch):
        files = provider._fetch_selected_files(provider_ref(), "main", candidates)

    assert [(f.path, f.content) for f in files] == [("a.md", "AAAA"), ("b.md", "B")]
    assert files[1].url == "https://github.com/user/repo/blob/main/b.md"
    assert calls == [("main", "a.md"), ("main", "blank.md"), ("main", "b.md")]


def test_fetch_selected_files_truncates_individual_files() -> None:
    provider = GitHubProvider()
    provider.max_file_chars = 3
    candidate = DocumentationCandidate(path="d.md", priority=0)
    with patch.object(provider, "_fetch_file_text", return_value="DDDDD"):
        files = provider._fetch_selected_files(provider_ref(), "main", [candidate])
    assert files[0].content == "DDD"


def test_fetch_file_text_requests_encoded_path_with_ref() -> None:
    """File paths are URL-encoded and the ref is passed as a query param."""
    provider = GitHubProvider()
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(encoded_file("# Hello")),
    ) as mock_get:
        text = provider._fetch_file_text(provider_ref(), "main", "docs/my file.md")
    assert text == "# Hello"
    assert mock_get.call_args.args == (
        "https://api.github.com/repos/user/repo/contents/docs/my%20file.md",
    )
    assert mock_get.call_args.kwargs["params"] == {"ref": "main"}


@pytest.mark.parametrize(
    "payload",
    [
        [{"type": "dir"}],
        {"type": "dir", "encoding": "base64", "content": "abc"},
    ],
)
def test_fetch_file_text_rejects_non_file_payload(payload: Any) -> None:
    provider = GitHubProvider()
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(payload),
    ):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._fetch_file_text(provider_ref(), "main", "docs/x.md")
    assert str(excinfo.value) == "GitHub path is not a file: docs/x.md"


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "file", "encoding": "raw", "content": "abc"},
        {"type": "file", "encoding": "base64", "content": 42},
    ],
)
def test_fetch_file_text_rejects_unavailable_content(payload: dict[str, Any]) -> None:
    provider = GitHubProvider()
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(payload),
    ):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._fetch_file_text(provider_ref(), "main", "docs/x.md")
    assert str(excinfo.value) == ("GitHub file content is not available through the API: docs/x.md")


def test_fetch_file_text_decodes_newline_wrapped_base64() -> None:
    """GitHub wraps base64 payloads with newlines before decoding."""
    provider = GitHubProvider()
    encoded = base64.b64encode(b"# Wrapped\ntext").decode("ascii")
    wrapped = "\n".join(encoded[i : i + 4] for i in range(0, len(encoded), 4))
    payload = {"type": "file", "encoding": "base64", "content": wrapped}
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(payload),
    ):
        text = provider._fetch_file_text(provider_ref(), "main", "docs/w.md")
    assert text == "# Wrapped\ntext"


def test_fetch_file_text_rejects_non_utf8_content() -> None:
    provider = GitHubProvider()
    encoded = base64.b64encode(b"\xff\xfe").decode("ascii")
    payload = {"type": "file", "encoding": "base64", "content": encoded}
    with patch(
        "obsidian_ai_tools.providers.github.requests.get",
        return_value=FakeResponse(payload),
    ):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._fetch_file_text(provider_ref(), "main", "docs/bin.md")
    assert str(excinfo.value) == "GitHub documentation file is not UTF-8 text: docs/bin.md"


# ---------------------------------------------------------------------------
# _build_content
# ---------------------------------------------------------------------------


def test_build_content_renders_exact_markdown() -> None:
    """Aggregated content follows the exact section layout."""
    provider = GitHubProvider()
    files = [
        DocumentationFile(
            path="README.md",
            content="# Title\n\nBody  ",
            url="https://github.com/user/repo/blob/main/README.md",
        ),
        DocumentationFile(
            path="docs/a.md",
            content="text",
            url="https://github.com/user/repo/blob/main/docs/a.md",
        ),
    ]
    expected = (
        "# GitHub Repository Documentation\n"
        "\n"
        "Repository: user/repo\n"
        "Reference: main\n"
        "\n"
        "Selected documentation files:\n"
        "- README.md\n"
        "- docs/a.md\n"
        "\n"
        "## README.md\n"
        "\n"
        "# Title\n"
        "\n"
        "Body\n"
        "\n"
        "## docs/a.md\n"
        "\n"
        "text"
    )
    assert provider._build_content(provider_ref(), "main", files) == expected
    assert files[0].markdown_reference == (
        "[README.md](https://github.com/user/repo/blob/main/README.md)"
    )


# ---------------------------------------------------------------------------
# _ingest
# ---------------------------------------------------------------------------


def test_github_provider_ingest_reports_unsupported_url() -> None:
    provider = GitHubProvider()
    with pytest.raises(GitHubRepositoryError) as excinfo:
        provider._ingest("https://gitlab.com/user/repo")
    assert str(excinfo.value) == ("Unsupported GitHub repository URL: https://gitlab.com/user/repo")


def test_github_provider_ingest_reports_missing_docs_exactly() -> None:
    """The no-documentation error spells out every accepted file pattern."""
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
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._ingest("https://github.com/user/repo")
    assert str(excinfo.value) == (
        "No documentation files found in user/repo. "
        "Expected files like README*, docs/**, CONTRIBUTING*, SECURITY*, "
        "CHANGELOG*, or package metadata."
    )


def test_github_provider_ingest_reports_empty_documentation() -> None:
    """Whitespace-only documentation triggers the unreadable-files error."""
    provider = GitHubProvider()
    responses = {
        "https://api.github.com/repos/user/repo": FakeResponse({"default_branch": "main"}),
        "https://api.github.com/repos/user/repo/contents": FakeResponse(
            [{"type": "file", "name": "README.md", "path": "README.md"}]
        ),
        "https://api.github.com/repos/user/repo/contents/README.md": FakeResponse(
            encoded_file("   \n  ")
        ),
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        return responses[url]

    with patch("obsidian_ai_tools.providers.github.requests.get", side_effect=fake_get):
        with pytest.raises(GitHubRepositoryError) as excinfo:
            provider._ingest("https://github.com/user/repo")
    assert str(excinfo.value) == ("Documentation files in user/repo were empty or unreadable.")


def test_github_provider_ingest_metadata_and_request_details() -> None:
    """Ingest fills every metadata field and issues correctly shaped requests."""
    provider = GitHubProvider()
    responses = {
        "https://api.github.com/repos/user/repo": FakeResponse({"default_branch": "main"}),
        "https://api.github.com/repos/user/repo/contents": FakeResponse(
            [
                {"type": "file", "name": "READMEZ.md", "path": "READMEZ.md", "size": 10},
                {"type": "file", "name": "README_A.md", "path": "README_A.md", "size": 10},
            ]
        ),
        "https://api.github.com/repos/user/repo/contents/README_A.md": FakeResponse(
            encoded_file("# A")
        ),
        "https://api.github.com/repos/user/repo/contents/READMEZ.md": FakeResponse(
            encoded_file("# Z")
        ),
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        assert kwargs["timeout"] == 30
        if "/contents" in url:
            assert kwargs["params"] == {"ref": "main"}
        return responses[url]

    with patch("obsidian_ai_tools.providers.github.requests.get", side_effect=fake_get):
        result = provider._ingest("https://github.com/user/repo")

    assert result.title == "user/repo repository documentation"
    assert result.author == "user"
    assert result.site_name == "GitHub Repository"
    assert result.published_date is None
    assert result.url == "https://github.com/user/repo"
    assert "Repository: user/repo" in result.content
    assert "Reference: main" in result.content
    assert result.source_references == [
        "[README_A.md](https://github.com/user/repo/blob/main/README_A.md)",
        "[READMEZ.md](https://github.com/user/repo/blob/main/READMEZ.md)",
    ]
