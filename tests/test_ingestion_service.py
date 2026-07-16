"""Tests for the shared ingestion orchestration service."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app
from obsidian_ai_tools.ingestion import (
    ContentFetchError,
    IngestionRequest,
    IngestionResult,
    NoteGenerationStageError,
    ProviderSelectionError,
    VaultWriteError,
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
    mock_write.assert_called_once_with(note=note, vault_path=tmp_path, inbox_folder="inbox")
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
