"""GitHub repository documentation ingestion provider."""

import base64
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import requests

from ..config import get_settings
from ..models import ArticleMetadata
from .base import BaseProvider

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
DOCS_DIRECTORIES = {"doc", "docs", "documentation"}
DOC_EXTENSIONS = {".adoc", ".md", ".markdown", ".mdx", ".rst", ".txt"}
ROOT_DOC_PREFIXES = (
    "readme",
    "contributing",
    "security",
    "changelog",
    "code_of_conduct",
    "license",
)
PACKAGE_METADATA_FILES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
}
PRIMARY_README_FILES = {
    "readme",
    "readme.adoc",
    "readme.md",
    "readme.markdown",
    "readme.mdx",
    "readme.rst",
    "readme.txt",
}


class GitHubRepositoryError(ValueError):
    """Raised when a GitHub repository cannot be ingested as documentation."""


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str
    ref: str | None = None
    docs_prefix: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass(frozen=True)
class DocumentationCandidate:
    path: str
    priority: int
    size: int | None = None


@dataclass(frozen=True)
class DocumentationFile:
    path: str
    content: str
    url: str

    @property
    def markdown_reference(self) -> str:
        return f"[{self.path}]({self.url})"


def parse_github_repo_url(source: str) -> GitHubRepoRef | None:
    """Parse supported GitHub repository URLs.

    Blob/raw file URLs intentionally return None so the existing web provider
    can keep handling single-file GitHub ingestion.
    """
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        return None

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not owner or not repo:
        return None

    if len(parts) == 2:
        return GitHubRepoRef(owner=owner, repo=repo)

    route = parts[2].lower()
    if route == "tree" and len(parts) >= 4:
        docs_prefix = "/".join(parts[4:]) if len(parts) > 4 else None
        return GitHubRepoRef(owner=owner, repo=repo, ref=parts[3], docs_prefix=docs_prefix)

    return None


class GitHubProvider(BaseProvider):
    """Provider for summarizing GitHub repositories from documentation files."""

    max_files = 12
    max_total_chars = 120_000
    max_file_chars = 40_000
    max_docs_depth = 3
    max_directory_entries = 200

    def __init__(self) -> None:
        self.github_token = self._load_token()

    @property
    def name(self) -> str:
        return "github"

    def validate(self, source: str) -> bool:
        """Check if source is a supported GitHub repository URL."""
        return parse_github_repo_url(source) is not None

    def _ingest(self, source: str, **kwargs: Any) -> ArticleMetadata:
        """Fetch bounded repository documentation and return aggregated metadata."""
        repo = parse_github_repo_url(source)
        if repo is None:
            raise GitHubRepositoryError(f"Unsupported GitHub repository URL: {source}")

        ref = repo.ref or self._fetch_default_branch(repo)
        candidates = self._discover_documentation_candidates(repo, ref)
        selected = sorted(candidates, key=lambda item: (item.priority, item.path.lower()))[
            : self.max_files
        ]
        if not selected:
            raise GitHubRepositoryError(
                f"No documentation files found in {repo.display_name}. "
                "Expected files like README*, docs/**, CONTRIBUTING*, SECURITY*, "
                "CHANGELOG*, or package metadata."
            )

        files = self._fetch_selected_files(repo, ref, selected)
        if not files:
            raise GitHubRepositoryError(
                f"Documentation files in {repo.display_name} were empty or unreadable."
            )

        content = self._build_content(repo, ref, files)
        return ArticleMetadata(
            url=repo.repository_url if repo.ref is None else f"{repo.repository_url}/tree/{ref}",
            title=f"{repo.display_name} repository documentation",
            author=repo.owner,
            site_name="GitHub Repository",
            published_date=None,
            content=content,
            source_type="github",
            source_references=[file.markdown_reference for file in files],
        )

    def _load_token(self) -> str | None:
        try:
            settings = get_settings()
            if settings.github_token:
                return settings.github_token
        except Exception:
            logger.debug("GitHub token unavailable from settings", exc_info=True)
        return os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def _github_get(self, url: str, params: dict[str, str] | None = None) -> Any:
        response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if response.status_code in {401, 403}:
            message = self._github_error_message(response)
            raise GitHubRepositoryError(message)
        if response.status_code == 404:
            raise GitHubRepositoryError(
                "GitHub repository or documentation path was not found. "
                "For private repositories, set GITHUB_TOKEN with repository read access."
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise GitHubRepositoryError(f"GitHub request failed: {exc}") from exc
        return response.json()

    def _github_error_message(self, response: requests.Response) -> str:
        remaining = response.headers.get("X-RateLimit-Remaining")
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message = str(payload.get("message") or response.text or "GitHub access denied")
        if remaining == "0" or "rate limit" in message.lower():
            return (
                "GitHub API rate limit exceeded while fetching repository documentation. "
                "Set GITHUB_TOKEN to use authenticated GitHub API access."
            )
        return (
            "GitHub repository requires access or credentials. "
            "Set GITHUB_TOKEN with repository read access and try again. "
            f"GitHub response: {message}"
        )

    def _fetch_default_branch(self, repo: GitHubRepoRef) -> str:
        data = self._github_get(f"{GITHUB_API_URL}/repos/{repo.owner}/{repo.repo}")
        default_branch = data.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise GitHubRepositoryError(
                f"Could not determine default branch for {repo.display_name}."
            )
        return default_branch

    def _list_directory(
        self, repo: GitHubRepoRef, ref: str, path: str | None = None
    ) -> list[dict[str, Any]]:
        encoded_path = quote(path or "", safe="/")
        url = f"{GITHUB_API_URL}/repos/{repo.owner}/{repo.repo}/contents"
        if encoded_path:
            url = f"{url}/{encoded_path}"
        data = self._github_get(url, params={"ref": ref})
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise GitHubRepositoryError(
                f"Unexpected GitHub contents response for {repo.display_name}."
            )
        return data[: self.max_directory_entries]

    def _discover_documentation_candidates(
        self, repo: GitHubRepoRef, ref: str
    ) -> list[DocumentationCandidate]:
        candidates: list[DocumentationCandidate] = []
        root_items = self._list_directory(repo, ref, repo.docs_prefix)
        for item in root_items:
            path = str(item.get("path") or "")
            name = str(item.get("name") or "")
            item_type = item.get("type")
            if item_type == "file" and repo.docs_prefix is not None and self._is_docs_file(name):
                candidates.append(
                    DocumentationCandidate(
                        path=path,
                        priority=self._priority_for_path(path),
                        size=item.get("size"),
                    )
                )
            elif item_type == "file" and self._is_root_documentation_file(name):
                candidates.append(
                    DocumentationCandidate(
                        path=path,
                        priority=self._priority_for_path(path),
                        size=item.get("size"),
                    )
                )
            elif item_type == "dir" and (
                repo.docs_prefix is not None or name.lower() in DOCS_DIRECTORIES
            ):
                self._collect_docs_directory(repo, ref, path, 1, candidates)
        return candidates

    def _collect_docs_directory(
        self,
        repo: GitHubRepoRef,
        ref: str,
        path: str,
        depth: int,
        candidates: list[DocumentationCandidate],
    ) -> None:
        if depth > self.max_docs_depth:
            return
        for item in self._list_directory(repo, ref, path):
            item_path = str(item.get("path") or "")
            item_name = str(item.get("name") or "")
            item_type = item.get("type")
            if item_type == "file" and self._is_docs_file(item_name):
                candidates.append(
                    DocumentationCandidate(
                        path=item_path,
                        priority=self._priority_for_path(item_path),
                        size=item.get("size"),
                    )
                )
            elif item_type == "dir":
                self._collect_docs_directory(repo, ref, item_path, depth + 1, candidates)

    def _is_root_documentation_file(self, name: str) -> bool:
        lower_name = name.lower()
        if lower_name in PACKAGE_METADATA_FILES:
            return True
        stem, _, extension = lower_name.rpartition(".")
        base_name = stem or lower_name
        return lower_name.endswith(tuple(DOC_EXTENSIONS)) and base_name.startswith(
            ROOT_DOC_PREFIXES
        )

    def _is_docs_file(self, name: str) -> bool:
        return name.lower().endswith(tuple(DOC_EXTENSIONS))

    def _priority_for_path(self, path: str) -> int:
        lower_path = path.lower()
        name = lower_path.rsplit("/", 1)[-1]
        if name in PRIMARY_README_FILES:
            return 0 if "/" not in lower_path else 20
        if name.startswith("readme"):
            return 5 if "/" not in lower_path else 25
        if name.startswith("contributing"):
            return 10
        if name.startswith("security"):
            return 11
        if name.startswith("changelog"):
            return 12
        if lower_path.startswith(("docs/", "doc/", "documentation/")):
            return 30
        if name in PACKAGE_METADATA_FILES:
            return 70
        return 90

    def _fetch_selected_files(
        self, repo: GitHubRepoRef, ref: str, selected: list[DocumentationCandidate]
    ) -> list[DocumentationFile]:
        files: list[DocumentationFile] = []
        total_chars = 0
        for candidate in selected:
            remaining = self.max_total_chars - total_chars
            if remaining <= 0:
                break
            content = self._fetch_file_text(repo, ref, candidate.path)
            if not content.strip():
                continue
            content = content[: min(self.max_file_chars, remaining)]
            total_chars += len(content)
            files.append(
                DocumentationFile(
                    path=candidate.path,
                    content=content,
                    url=f"{repo.repository_url}/blob/{ref}/{candidate.path}",
                )
            )
        return files

    def _fetch_file_text(self, repo: GitHubRepoRef, ref: str, path: str) -> str:
        encoded_path = quote(path, safe="/")
        data = self._github_get(
            f"{GITHUB_API_URL}/repos/{repo.owner}/{repo.repo}/contents/{encoded_path}",
            params={"ref": ref},
        )
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubRepositoryError(f"GitHub path is not a file: {path}")
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise GitHubRepositoryError(
                f"GitHub file content is not available through the API: {path}"
            )
        encoded = data["content"].replace("\n", "")
        try:
            return base64.b64decode(encoded).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitHubRepositoryError(
                f"GitHub documentation file is not UTF-8 text: {path}"
            ) from exc

    def _build_content(self, repo: GitHubRepoRef, ref: str, files: list[DocumentationFile]) -> str:
        selected = "\n".join(f"- {file.path}" for file in files)
        sections = [
            "# GitHub Repository Documentation",
            "",
            f"Repository: {repo.display_name}",
            f"Reference: {ref}",
            "",
            "Selected documentation files:",
            selected,
            "",
        ]
        for file in files:
            sections.extend([f"## {file.path}", "", file.content.strip(), ""])
        return "\n".join(sections).strip()
