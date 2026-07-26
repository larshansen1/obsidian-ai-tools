"""Tests for the shared ingestion orchestration service."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app
from obsidian_ai_tools.dedup import ExistingNote
from obsidian_ai_tools.ingestion import (
    ContentFetchError,
    IngestionRequest,
    IngestionResult,
    NoteGenerationStageError,
    ProviderSelectionError,
    VaultWriteError,
    default_prompt_version,
    ingest_content,
)
from obsidian_ai_tools.models import ArticleMetadata, CostInfo, Note
from obsidian_ai_tools.server.app import create_app

runner = CliRunner()


def _settings(vault_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        obsidian_vault_path=vault_path,
        obsidian_inbox_folder="inbox",
        llm_model="test-model",
        openrouter_api_key="test-key",
        max_transcript_length=1234,
    )


def _metadata() -> ArticleMetadata:
    return ArticleMetadata(
        url="https://example.com/article",
        title="Example",
        content="Example content",
    )


def _note() -> Note:
    return Note(
        title="Generated Note",
        summary="Summary",
        tags=["test"],
        source_url="https://example.com/article",
        source_type="web",
        model="test-model",
        prompt_version="article_v1",
    )


def _cost_info() -> CostInfo:
    return CostInfo(
        model="test-model",
        source_type="web",
        input_tokens=100,
        output_tokens=50,
        total_cost_usd=0.001,
        source_url="https://example.com/article",
    )


def test_default_prompt_version_uses_github_repo_prompt() -> None:
    """GitHub repository ingestion should use the repo-specific prompt by default."""
    assert default_prompt_version("github") == "github_repo_v1"


def test_ingest_content_runs_shared_pipeline_and_emits_progress(tmp_path: Path) -> None:
    """Test the service coordinates provider, LLM, and vault persistence."""
    metadata = _metadata()
    note = _note()
    note_path = tmp_path / "inbox" / "web-generated-note.md"
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    stages: list[str] = []

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch(
            "obsidian_ai_tools.ingestion.generate_note", return_value=(note, _cost_info())
        ) as mock_generate,
        patch("obsidian_ai_tools.ingestion.write_note", return_value=note_path) as mock_write,
    ):
        result = ingest_content(
            IngestionRequest(url=metadata.url),
            _settings(tmp_path),  # type: ignore[arg-type]
            on_progress=lambda progress: stages.append(progress.stage),
        )

    assert result == IngestionResult(
        provider_name="web",
        prompt_version="article_v1",
        metadata=metadata,
        note=note,
        file_path=note_path,
    )
    provider.ingest.assert_called_once_with(metadata.url)
    mock_generate.assert_called_once_with(
        metadata=metadata,
        model="test-model",
        api_key="test-key",
        existing_tags=None,
        max_content_length=1234,
        prompt_version="article_v1",
    )
    mock_write.assert_called_once_with(
        note=note, vault_path=tmp_path, inbox_folder="inbox", target_path=None
    )
    assert stages == [
        "provider_selected",
        "fetching",
        "content_fetched",
        "generating",
        "note_generated",
        "writing",
        "note_written",
    ]


@pytest.mark.parametrize(
    ("provider_name", "ingestion_request", "expected_kwargs"),
    [
        ("pdf", IngestionRequest(url="document.pdf", max_pages=12), {"max_pages": 12}),
        (
            "youtube",
            IngestionRequest(
                url="https://youtube.com/watch?v=test",
                transcript_providers="supadata,direct",
            ),
            {"provider_order": "supadata,direct"},
        ),
        (
            "web",
            IngestionRequest(
                url="https://chatgpt.com/c/example",
                captured_content="User: question\nAssistant: answer",
                captured_title="ChatGPT - Example",
            ),
            {
                "captured_content": "User: question\nAssistant: answer",
                "captured_title": "ChatGPT - Example",
            },
        ),
    ],
)
def test_ingest_content_forwards_provider_specific_options(
    tmp_path: Path,
    provider_name: str,
    ingestion_request: IngestionRequest,
    expected_kwargs: dict[str, int | str],
) -> None:
    """Test the shared service owns provider-specific option routing."""
    metadata = _metadata()
    provider = SimpleNamespace(name=provider_name, ingest=MagicMock(return_value=metadata))

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", return_value=(_note(), _cost_info())),
        patch("obsidian_ai_tools.ingestion.write_note", return_value=tmp_path / "note.md"),
    ):
        ingest_content(ingestion_request, _settings(tmp_path))  # type: ignore[arg-type]

    provider.ingest.assert_called_once_with(ingestion_request.url, **expected_kwargs)


def _write_existing_note(vault: Path, source_url: str) -> Path:
    note_path = vault / "inbox" / "web-existing-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        f"""---
title: Existing Note
tags:
  - test
created: 2026-07-19T10:00:00
type: source-note
source_type: web
source_url: {source_url}
model: test-model
prompt_version: article_v1
---

# Existing Note
""",
        encoding="utf-8",
    )
    return note_path


def test_ingest_content_skips_duplicate_source_before_fetch(tmp_path: Path) -> None:
    """Test a known source returns the existing note without fetch or LLM."""
    metadata = _metadata()
    existing_path = _write_existing_note(tmp_path, metadata.url)
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note") as mock_generate,
    ):
        result = ingest_content(
            IngestionRequest(url=metadata.url),
            _settings(tmp_path),  # type: ignore[arg-type]
        )

    assert result == ExistingNote(
        file_path=existing_path,
        title="Existing Note",
        tags=["test"],
        source_type="web",
    )
    provider.ingest.assert_not_called()
    mock_generate.assert_not_called()


def test_ingest_content_update_overwrites_existing_file(tmp_path: Path) -> None:
    """Test update=True reruns the pipeline into the existing file path."""
    metadata = _metadata()
    existing_path = _write_existing_note(tmp_path, metadata.url)
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", return_value=(_note(), _cost_info())),
        patch("obsidian_ai_tools.ingestion.write_note", return_value=existing_path) as mock_write,
    ):
        result = ingest_content(
            IngestionRequest(url=metadata.url, update=True),
            _settings(tmp_path),  # type: ignore[arg-type]
        )

    assert isinstance(result, IngestionResult)
    assert result.file_path == existing_path
    mock_write.assert_called_once_with(
        note=result.note,
        vault_path=tmp_path,
        inbox_folder="inbox",
        target_path=existing_path,
    )


def test_ingest_content_wraps_provider_selection_failure(tmp_path: Path) -> None:
    """Test unsupported sources produce a service-level selection error."""
    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            side_effect=ValueError("unsupported"),
        ),
        pytest.raises(ProviderSelectionError),
    ):
        ingest_content(IngestionRequest(url="unsupported"), _settings(tmp_path))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("target", "error_type"),
    [
        ("provider", ContentFetchError),
        ("generate", NoteGenerationStageError),
        ("write", VaultWriteError),
    ],
)
def test_ingest_content_wraps_stage_failures(
    tmp_path: Path, target: str, error_type: type[Exception]
) -> None:
    """Test adapters can distinguish failures from each pipeline stage."""
    metadata = _metadata()
    note = _note()
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    generate = MagicMock(return_value=(note, _cost_info()))
    write = MagicMock(return_value=tmp_path / "note.md")

    if target == "provider":
        provider.ingest.side_effect = RuntimeError("fetch failed")
    elif target == "generate":
        generate.side_effect = RuntimeError("generation failed")
    else:
        write.side_effect = RuntimeError("write failed")

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", generate),
        patch("obsidian_ai_tools.ingestion.write_note", write),
        pytest.raises(error_type),
    ):
        ingest_content(IngestionRequest(url=metadata.url), _settings(tmp_path))  # type: ignore[arg-type]


def test_http_ingest_delegates_to_shared_pipeline(tmp_path: Path) -> None:
    """Test the webhook adapter passes request options to the shared service."""
    metadata = _metadata()
    note = _note()
    note_path = tmp_path / "inbox" / "web-generated-note.md"
    result = IngestionResult(
        provider_name="web",
        prompt_version="article_v1",
        metadata=metadata,
        note=note,
        file_path=note_path,
    )

    with (
        patch("obsidian_ai_tools.server.app.get_settings", return_value=_settings(tmp_path)),
        patch("obsidian_ai_tools.server.app.ingest_content", return_value=result) as mock_ingest,
    ):
        response = TestClient(create_app()).post(
            "/ingest",
            json={
                "url": metadata.url,
                "vault_path": str(tmp_path),
                "prompt_version": "article_v1",
                "max_pages": 12,
                "captured_content": "User: question\nAssistant: answer",
                "captured_title": "ChatGPT - Example",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == note.title
    assert body["obsidian_url"] == (
        f"obsidian://open?vault={tmp_path.name}&file=inbox%2Fweb-generated-note.md"
    )
    request = mock_ingest.call_args.args[0]
    assert request == IngestionRequest(
        url=metadata.url,
        vault_path=tmp_path,
        prompt_version="article_v1",
        max_pages=12,
        captured_content="User: question\nAssistant: answer",
        captured_title="ChatGPT - Example",
    )


def test_http_ingest_reports_existing_source(tmp_path: Path) -> None:
    """Test the webhook adapter surfaces a duplicate as status=exists."""
    existing = ExistingNote(
        file_path=tmp_path / "inbox" / "web-existing-note.md",
        title="Existing Note",
        tags=["test"],
        source_type="web",
    )

    with (
        patch("obsidian_ai_tools.server.app.get_settings", return_value=_settings(tmp_path)),
        patch("obsidian_ai_tools.server.app.ingest_content", return_value=existing),
    ):
        response = TestClient(create_app()).post(
            "/ingest",
            json={"url": "https://example.com/article", "vault_path": str(tmp_path)},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "exists"
    assert body["title"] == "Existing Note"
    assert body["obsidian_url"] == (
        f"obsidian://open?vault={tmp_path.name}&file=inbox%2Fweb-existing-note.md"
    )


def test_http_lookup_reports_existing_note(tmp_path: Path) -> None:
    """Test /lookup surfaces a matching note without running the pipeline."""
    existing = ExistingNote(
        file_path=tmp_path / "inbox" / "web-existing-note.md",
        title="Existing Note",
        tags=["test"],
        source_type="web",
    )

    with (
        patch("obsidian_ai_tools.server.app.get_settings", return_value=_settings(tmp_path)),
        patch(
            "obsidian_ai_tools.server.app.find_note_by_source", return_value=existing
        ) as mock_find,
        patch("obsidian_ai_tools.server.app.ingest_content") as mock_ingest,
    ):
        response = TestClient(create_app()).get(
            "/lookup", params={"url": "https://example.com/article"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["title"] == "Existing Note"
    assert body["tags"] == ["test"]
    assert body["source_type"] == "web"
    assert body["obsidian_url"] == (
        f"obsidian://open?vault={tmp_path.name}&file=inbox%2Fweb-existing-note.md"
    )
    mock_find.assert_called_once_with(tmp_path, "https://example.com/article")
    mock_ingest.assert_not_called()


def test_http_lookup_reports_missing_note(tmp_path: Path) -> None:
    """Test /lookup returns exists=false when no note matches the source."""
    with (
        patch("obsidian_ai_tools.server.app.get_settings", return_value=_settings(tmp_path)),
        patch("obsidian_ai_tools.server.app.find_note_by_source", return_value=None),
    ):
        response = TestClient(create_app()).get(
            "/lookup", params={"url": "https://example.com/unknown"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "exists": False,
        "title": None,
        "file_path": None,
        "tags": [],
        "source_type": None,
        "obsidian_url": None,
    }


def test_http_lookup_requires_url(tmp_path: Path) -> None:
    """Test /lookup rejects a request with no url parameter."""
    with patch("obsidian_ai_tools.server.app.get_settings", return_value=_settings(tmp_path)):
        response = TestClient(create_app()).get("/lookup")

    assert response.status_code == 422


def test_cli_ingest_reports_existing_source(tmp_path: Path) -> None:
    """Test the CLI adapter prints the skip message for duplicates."""
    existing = ExistingNote(
        file_path=tmp_path / "inbox" / "web-existing-note.md",
        title="Existing Note",
        tags=["test"],
        source_type="web",
    )

    with (
        patch("obsidian_ai_tools.commands.ingest.setup_logging"),
        patch("obsidian_ai_tools.commands.ingest.get_settings", return_value=_settings(tmp_path)),
        patch("obsidian_ai_tools.commands.ingest.ingest_content", return_value=existing),
    ):
        response = runner.invoke(app, ["ingest", "https://example.com/article"])

    assert response.exit_code == 0
    assert "Already in vault" in response.output
    assert "--update" in response.output
    assert "Ingestion complete" not in response.output


def test_cors_allows_chrome_extension_origin() -> None:
    """Test preflight from the extension origin passes CORS."""
    response = TestClient(create_app()).options(
        "/ingest",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "chrome-extension://abcdefghijklmnop"


def test_cors_rejects_web_origins() -> None:
    """Test preflight from a regular web page is rejected."""
    response = TestClient(create_app()).options(
        "/ingest",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cli_ingest_delegates_to_shared_pipeline(tmp_path: Path) -> None:
    """Test the CLI adapter passes options to the shared service."""
    metadata = _metadata()
    note = _note()
    result = IngestionResult(
        provider_name="web",
        prompt_version="article_v1",
        metadata=metadata,
        note=note,
        file_path=tmp_path / "inbox" / "web-generated-note.md",
    )

    with (
        patch("obsidian_ai_tools.commands.ingest.setup_logging"),
        patch("obsidian_ai_tools.commands.ingest.get_settings", return_value=_settings(tmp_path)),
        patch(
            "obsidian_ai_tools.commands.ingest.ingest_content", return_value=result
        ) as mock_ingest,
    ):
        response = runner.invoke(
            app,
            [
                "ingest",
                metadata.url,
                "--vault",
                str(tmp_path),
                "--prompt-version",
                "article_v1",
                "--max-pages",
                "12",
            ],
        )

    assert response.exit_code == 0
    assert "Ingestion complete" in response.output
    request = mock_ingest.call_args.args[0]
    assert request == IngestionRequest(
        url=metadata.url,
        vault_path=tmp_path,
        prompt_version="article_v1",
        max_pages=12,
    )
