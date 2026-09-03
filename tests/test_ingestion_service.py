"""Tests for the shared ingestion orchestration service."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app
from obsidian_ai_tools.dedup import ExistingNote
from obsidian_ai_tools.ingestion import (
    ContentFetchError,
    IngestionProgress,
    IngestionRequest,
    IngestionResult,
    NoteGenerationStageError,
    ProviderSelectionError,
    VaultWriteError,
    _discover_existing_tags,
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
        llm_base_url="https://openrouter.ai/api/v1",
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


def test_default_prompt_version_maps_every_provider_and_fallback() -> None:
    """Each provider maps to its own default template; unknown sources fall back."""
    assert default_prompt_version("youtube") == "youtube_v2"
    assert default_prompt_version("file") == "markdown_v1"
    assert default_prompt_version("pdf") == "pdf_v1"
    assert default_prompt_version("unknown-provider") == "article_v1"


def test_ingest_content_runs_shared_pipeline_and_emits_progress(tmp_path: Path) -> None:
    """Test the service coordinates provider, LLM, and vault persistence."""
    metadata = _metadata()
    note = _note()
    note_path = tmp_path / "inbox" / "web-generated-note.md"
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    stages: list[str] = []

    logger = MagicMock()
    (tmp_path / "inbox").mkdir(exist_ok=True)
    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch(
            "obsidian_ai_tools.ingestion.generate_note", return_value=(note, _cost_info())
        ) as mock_generate,
        patch("obsidian_ai_tools.ingestion.write_note", return_value=note_path) as mock_write,
        patch("obsidian_ai_tools.ingestion.logging") as mock_logging,
    ):
        mock_logging.getLogger.return_value = logger
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
        base_url="https://openrouter.ai/api/v1",
    )
    mock_write.assert_called_once_with(
        note=note, vault_path=tmp_path, inbox_folder="inbox", target_path=None
    )
    logger.info.assert_called_once_with(
        "Note persisted to vault",
        extra={
            "file_path": str(note_path),
            "title": "Generated Note",
            "url": "https://example.com/article",
        },
    )
    assert call("obsidian_ai_tools.ingestion") in mock_logging.getLogger.call_args_list
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
    logger = MagicMock()

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note") as mock_generate,
        patch("obsidian_ai_tools.ingestion.logging") as mock_logging,
    ):
        mock_logging.getLogger.return_value = logger
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
    logger.info.assert_called_once_with(
        "Source already in vault; skipping ingestion",
        extra={"file_path": str(existing_path), "url": metadata.url},
    )
    mock_logging.getLogger.assert_called_once_with("obsidian_ai_tools.ingestion")


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
        pytest.raises(ProviderSelectionError) as exc_info,
    ):
        ingest_content(IngestionRequest(url="unsupported"), _settings(tmp_path))  # type: ignore[arg-type]

    assert str(exc_info.value) == "No provider for source: unsupported"


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

    expected_message = {
        "provider": "Content fetch failed: fetch failed",
        "generate": "Note generation failed: generation failed",
        "write": "Vault write failed: write failed",
    }[target]

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", generate),
        patch("obsidian_ai_tools.ingestion.write_note", write),
        pytest.raises(error_type) as exc_info,
    ):
        ingest_content(IngestionRequest(url=metadata.url), _settings(tmp_path))  # type: ignore[arg-type]

    assert str(exc_info.value) == expected_message


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


def test_ingest_content_emits_full_progress_payloads(tmp_path: Path) -> None:
    """Every progress event carries the exact provider/metadata/note payload."""
    metadata = _metadata()
    note = _note()
    note_path = tmp_path / "inbox" / "web-generated-note.md"
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    progress: list[IngestionProgress] = []

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", return_value=(note, _cost_info())),
        patch("obsidian_ai_tools.ingestion.write_note", return_value=note_path),
    ):
        ingest_content(
            IngestionRequest(
                url=metadata.url,
                prompt_version="markdown_v1",
                transcript_providers="supadata,direct",
            ),
            _settings(tmp_path),  # type: ignore[arg-type]
            on_progress=progress.append,
        )

    assert progress == [
        IngestionProgress(
            stage="provider_selected", provider_name="web", prompt_version="markdown_v1"
        ),
        IngestionProgress(
            stage="fetching",
            provider_name="web",
            prompt_version="markdown_v1",
            transcript_providers="supadata,direct",
        ),
        IngestionProgress(
            stage="content_fetched",
            provider_name="web",
            prompt_version="markdown_v1",
            metadata=metadata,
        ),
        IngestionProgress(stage="generating", provider_name="web", prompt_version="markdown_v1"),
        IngestionProgress(
            stage="note_generated",
            provider_name="web",
            prompt_version="markdown_v1",
            note=note,
        ),
        IngestionProgress(
            stage="writing",
            provider_name="web",
            prompt_version="markdown_v1",
            note=note,
        ),
        IngestionProgress(
            stage="note_written",
            provider_name="web",
            prompt_version="markdown_v1",
            note=note,
            file_path=note_path,
        ),
    ]


def test_ingest_content_forwards_discovered_tags_to_llm(tmp_path: Path) -> None:
    """Existing-vault tags are discovered before generation and passed along."""
    metadata = _metadata()
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    discovered = "- ai (2 notes)\n- python (1 notes)"

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch(
            "obsidian_ai_tools.ingestion._discover_existing_tags", return_value=discovered
        ) as mock_discover,
        patch(
            "obsidian_ai_tools.ingestion.generate_note", return_value=(_note(), _cost_info())
        ) as mock_generate,
        patch("obsidian_ai_tools.ingestion.write_note", return_value=tmp_path / "note.md"),
    ):
        ingest_content(
            IngestionRequest(url=metadata.url, prompt_version="youtube_v2"),
            _settings(tmp_path),  # type: ignore[arg-type]
        )

    mock_discover.assert_called_once_with(tmp_path, "youtube_v2")
    assert mock_generate.call_args.kwargs["existing_tags"] == discovered


def test_ingest_content_uses_provider_default_prompt_version(tmp_path: Path) -> None:
    """A request without prompt_version falls back to the provider default."""
    metadata = _metadata()
    provider = SimpleNamespace(name="youtube", ingest=MagicMock(return_value=metadata))

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch(
            "obsidian_ai_tools.ingestion.generate_note", return_value=(_note(), _cost_info())
        ) as mock_generate,
        patch("obsidian_ai_tools.ingestion.write_note", return_value=tmp_path / "note.md"),
    ):
        result = ingest_content(
            IngestionRequest(url="https://youtube.com/watch?v=abc"),
            _settings(tmp_path),  # type: ignore[arg-type]
        )

    assert isinstance(result, IngestionResult)
    assert result.prompt_version == "youtube_v2"
    assert mock_generate.call_args.kwargs["prompt_version"] == "youtube_v2"


def test_ingest_content_records_llm_cost(tmp_path: Path) -> None:
    """Successful generation records cost through the observability DB."""
    metadata = _metadata()
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    mock_db = MagicMock()

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", return_value=(_note(), _cost_info())),
        patch("obsidian_ai_tools.ingestion.write_note", return_value=tmp_path / "note.md"),
        patch("obsidian_ai_tools.observability.get_db", return_value=mock_db),
    ):
        ingest_content(
            IngestionRequest(url=metadata.url, prompt_version="markdown_v1"),
            _settings(tmp_path),  # type: ignore[arg-type]
        )

    mock_db.record_cost.assert_called_once_with(
        operation="ingest",
        model="test-model",
        source_type="web",
        input_tokens=100,
        output_tokens=50,
        total_cost_usd=0.001,
        source_url="https://example.com/article",
    )


def test_ingest_content_survives_cost_record_failure(tmp_path: Path) -> None:
    """Cost recording errors degrade to a debug log, never failing ingestion."""
    metadata = _metadata()
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    mock_db = MagicMock()
    mock_db.record_cost.side_effect = RuntimeError("db is down")
    logger = MagicMock()

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", return_value=(_note(), _cost_info())),
        patch("obsidian_ai_tools.ingestion.write_note", return_value=tmp_path / "note.md"),
        patch("obsidian_ai_tools.observability.get_db", return_value=mock_db),
        patch("obsidian_ai_tools.ingestion.logging") as mock_logging,
    ):
        mock_logging.getLogger.return_value = logger
        result = ingest_content(
            IngestionRequest(url=metadata.url, prompt_version="markdown_v1"),
            _settings(tmp_path),  # type: ignore[arg-type]
        )

    assert isinstance(result, IngestionResult)
    logger.debug.assert_called_once_with("Failed to record cost: db is down")
    assert call("obsidian_ai_tools.ingestion") in mock_logging.getLogger.call_args_list


def test_ingest_content_continues_after_duplicate_scan_failure(tmp_path: Path) -> None:
    """A failed dedup scan warns and proceeds instead of blocking ingestion."""
    metadata = _metadata()
    note = _note()
    note_path = tmp_path / "inbox" / "web-generated-note.md"
    provider = SimpleNamespace(name="web", ingest=MagicMock(return_value=metadata))
    logger = MagicMock()

    with (
        patch(
            "obsidian_ai_tools.ingestion.ProviderFactory.get_provider",
            return_value=provider,
        ),
        patch(
            "obsidian_ai_tools.ingestion.find_note_by_source",
            side_effect=RuntimeError("scan failed"),
        ),
        patch("obsidian_ai_tools.ingestion.generate_note", return_value=(note, _cost_info())),
        patch("obsidian_ai_tools.ingestion.write_note", return_value=note_path),
        patch("obsidian_ai_tools.ingestion.logging") as mock_logging,
    ):
        mock_logging.getLogger.return_value = logger
        result = ingest_content(
            IngestionRequest(url=metadata.url, prompt_version="markdown_v1"),
            _settings(tmp_path),  # type: ignore[arg-type]
        )

    assert result == IngestionResult(
        provider_name="web",
        prompt_version="markdown_v1",
        metadata=metadata,
        note=note,
        file_path=note_path,
    )
    logger.warning.assert_called_once_with(
        "Duplicate scan failed; proceeding with ingestion", exc_info=True
    )
    assert call("obsidian_ai_tools.ingestion") in mock_logging.getLogger.call_args_list


@pytest.mark.parametrize(
    ("provider_name", "ingestion_request"),
    [
        ("pdf", IngestionRequest(url="document.pdf")),
        ("youtube", IngestionRequest(url="https://youtube.com/watch?v=test")),
        ("web", IngestionRequest(url="https://example.com/article", max_pages=12)),
    ],
)
def test_ingest_content_omits_unset_provider_options(
    tmp_path: Path,
    provider_name: str,
    ingestion_request: IngestionRequest,
) -> None:
    """Provider options are only forwarded when the provider supports them."""
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

    provider.ingest.assert_called_once_with(ingestion_request.url)


def test_discover_existing_tags_skipped_for_legacy_prompts(tmp_path: Path) -> None:
    """Non-v2, non-article prompts skip tag discovery entirely."""
    with (
        patch("obsidian_ai_tools.indexer.build_index") as mock_index,
        patch("obsidian_ai_tools.search.list_all_tags") as mock_tags,
    ):
        assert _discover_existing_tags(tmp_path, "youtube_v1") is None
        assert _discover_existing_tags(tmp_path, "markdown_v1") is None

    mock_index.assert_not_called()
    mock_tags.assert_not_called()


def test_discover_existing_tags_skips_no_v2_article_prompt_without_calls(
    tmp_path: Path,
) -> None:
    """A prompt that lacks both triggers is never scanned."""
    with (
        patch("obsidian_ai_tools.indexer.build_index") as mock_index,
        patch("obsidian_ai_tools.search.list_all_tags"),
    ):
        assert _discover_existing_tags(tmp_path, "plain_v1") is None
    mock_index.assert_not_called()


def test_discover_existing_tags_builds_tag_list_for_v2_prompt(tmp_path: Path) -> None:
    """v2 prompts scan the inbox and render a compact tag list."""
    index = MagicMock()
    with (
        patch("obsidian_ai_tools.indexer.build_index", return_value=index) as mock_index,
        patch(
            "obsidian_ai_tools.search.list_all_tags",
            return_value={"ai": 3, "python": 1},
        ) as mock_tags,
    ):
        result = _discover_existing_tags(tmp_path, "youtube_v2")

    mock_index.assert_called_once_with(tmp_path, "inbox")
    mock_tags.assert_called_once_with(index)
    assert result == "- ai (3 notes)\n- python (1 notes)"


def test_discover_existing_tags_runs_for_article_prompts(tmp_path: Path) -> None:
    """article* prompts also qualify for tag discovery."""
    with (
        patch("obsidian_ai_tools.indexer.build_index", return_value=MagicMock()),
        patch(
            "obsidian_ai_tools.search.list_all_tags",
            return_value={"web": 7},
        ) as mock_tags,
    ):
        assert _discover_existing_tags(tmp_path, "article_v1") == "- web (7 notes)"
    mock_tags.assert_called_once()


def test_discover_existing_tags_truncates_to_twenty_tags(tmp_path: Path) -> None:
    """Only the first 20 tags are included in the rendered list."""
    many = {f"tag{i}": i for i in range(25)}
    with (
        patch("obsidian_ai_tools.indexer.build_index", return_value=MagicMock()),
        patch("obsidian_ai_tools.search.list_all_tags", return_value=many),
    ):
        result = _discover_existing_tags(tmp_path, "article_v2")

    assert result is not None
    assert "- tag0 (0 notes)" in result
    assert "- tag19 (19 notes)" in result
    assert "- tag20 (20 notes)" not in result
    assert result.count("\n") == 19


def test_discover_existing_tags_returns_none_without_tags(tmp_path: Path) -> None:
    """An inbox with no tags yields None rather than an empty list."""
    with (
        patch("obsidian_ai_tools.indexer.build_index", return_value=MagicMock()),
        patch("obsidian_ai_tools.search.list_all_tags", return_value={}),
    ):
        assert _discover_existing_tags(tmp_path, "youtube_v2") is None


def test_discover_existing_tags_swallows_scan_failures(tmp_path: Path) -> None:
    """A failing index build warns and returns None so ingestion continues."""
    logger = MagicMock()
    with (
        patch(
            "obsidian_ai_tools.indexer.build_index",
            side_effect=RuntimeError("index boom"),
        ),
        patch("obsidian_ai_tools.ingestion.logging") as mock_logging,
    ):
        mock_logging.getLogger.return_value = logger
        assert _discover_existing_tags(tmp_path, "article_v1") is None

    logger.warning.assert_called_once_with(
        "Failed to discover existing tags; generating note without them",
        exc_info=True,
    )
    assert mock_logging.getLogger.call_args == call("obsidian_ai_tools.ingestion")
