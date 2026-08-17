"""Shared orchestration for content ingestion entry points."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import Settings
from .dedup import ExistingNote, find_note_by_source
from .llm import generate_note
from .models import ArticleMetadata, Note, VideoMetadata
from .obsidian import write_note
from .providers.factory import ProviderFactory

Metadata = VideoMetadata | ArticleMetadata
ProgressStage = Literal[
    "provider_selected",
    "fetching",
    "content_fetched",
    "generating",
    "note_generated",
    "writing",
    "note_written",
]


class IngestionError(Exception):
    """Base exception for failures in the ingestion pipeline."""


class ProviderSelectionError(IngestionError):
    """Raised when no provider supports the requested source."""


class ContentFetchError(IngestionError):
    """Raised when a provider cannot fetch content metadata."""


class NoteGenerationStageError(IngestionError):
    """Raised when the LLM cannot generate a note."""


class VaultWriteError(IngestionError):
    """Raised when the generated note cannot be persisted."""


@dataclass(frozen=True)
class IngestionRequest:
    """Inputs accepted by every ingestion entry point."""

    url: str
    vault_path: Path | None = None
    prompt_version: str | None = None
    transcript_providers: str | None = None
    max_pages: int | None = None
    captured_content: str | None = None
    captured_title: str | None = None
    update: bool = False


@dataclass(frozen=True)
class IngestionProgress:
    """Progress emitted while a shared ingestion request is processed."""

    stage: ProgressStage
    provider_name: str
    prompt_version: str
    metadata: Metadata | None = None
    note: Note | None = None
    file_path: Path | None = None
    transcript_providers: str | None = None


@dataclass(frozen=True)
class IngestionResult:
    """Successful output from the shared ingestion pipeline."""

    provider_name: str
    prompt_version: str
    metadata: Metadata
    note: Note
    file_path: Path


def _discover_existing_tags(vault_path: Path, prompt_version: str) -> str | None:
    if not ("_v2" in prompt_version or prompt_version.startswith("article")):
        return None
    try:
        from .indexer import build_index
        from .search import list_all_tags

        vault_index = build_index(vault_path, "inbox")
        tag_counts = list_all_tags(vault_index)
        if tag_counts:
            tag_items = list(tag_counts.items())[:20]
            return "\n".join(f"- {tag} ({count} notes)" for tag, count in tag_items)
    except Exception:
        logging.getLogger("obsidian_ai_tools.ingestion").warning(
            "Failed to discover existing tags; generating note without them",
            exc_info=True,
        )
    return None


def default_prompt_version(provider_name: str) -> str:
    """Return the default prompt template for a provider."""
    return {
        "youtube": "youtube_v2",
        "file": "markdown_v1",
        "github": "github_repo_v1",
        "pdf": "pdf_v1",
    }.get(provider_name, "article_v1")


def ingest_content(
    request: IngestionRequest,
    settings: Settings,
    on_progress: Callable[[IngestionProgress], None] | None = None,
) -> IngestionResult | ExistingNote:
    """Fetch a source, generate a note, and persist it to the vault.

    If the source already exists in the vault (matched by normalized
    source_url) and request.update is False, returns the ExistingNote without
    fetching or calling the LLM. With request.update the pipeline runs fully
    and overwrites the existing file, keeping its name.
    """
    try:
        provider = ProviderFactory.get_provider(request.url)
    except ValueError as exc:
        raise ProviderSelectionError(f"No provider for source: {request.url}") from exc

    prompt_version = request.prompt_version or default_prompt_version(provider.name)
    vault_path = request.vault_path or settings.obsidian_vault_path

    existing: ExistingNote | None = None
    try:
        existing = find_note_by_source(vault_path, request.url)
    except Exception:
        # Dedup is an optimization; a scan failure must never block ingestion.
        logging.getLogger("obsidian_ai_tools.ingestion").warning(
            "Duplicate scan failed; proceeding with ingestion", exc_info=True
        )
    if existing is not None and not request.update:
        logging.getLogger("obsidian_ai_tools.ingestion").info(
            "Source already in vault; skipping ingestion",
            extra={"file_path": str(existing.file_path), "url": request.url},
        )
        return existing

    def emit(
        stage: ProgressStage,
        metadata: Metadata | None = None,
        note: Note | None = None,
        file_path: Path | None = None,
        transcript_providers: str | None = None,
    ) -> None:
        if on_progress is not None:
            on_progress(
                IngestionProgress(
                    stage=stage,
                    provider_name=provider.name,
                    prompt_version=prompt_version,
                    metadata=metadata,
                    note=note,
                    file_path=file_path,
                    transcript_providers=transcript_providers,
                )
            )

    emit("provider_selected")
    emit("fetching", transcript_providers=request.transcript_providers)

    kwargs: dict[str, int | str] = {}
    if provider.name == "pdf" and request.max_pages is not None:
        kwargs["max_pages"] = request.max_pages
    if provider.name == "youtube" and request.transcript_providers is not None:
        kwargs["provider_order"] = request.transcript_providers
    if provider.name == "web" and request.captured_content is not None:
        kwargs["captured_content"] = request.captured_content
        if request.captured_title is not None:
            kwargs["captured_title"] = request.captured_title

    try:
        metadata = provider.ingest(request.url, **kwargs)
    except Exception as exc:
        raise ContentFetchError(f"Content fetch failed: {exc}") from exc

    emit("content_fetched", metadata=metadata)
    existing_tags = _discover_existing_tags(vault_path, prompt_version)
    emit("generating")
    try:
        note, cost_info = generate_note(
            metadata=metadata,
            model=settings.llm_model,
            api_key=settings.openrouter_api_key,
            existing_tags=existing_tags,
            max_content_length=settings.max_transcript_length,
            prompt_version=prompt_version,
            base_url=settings.llm_base_url,
        )
    except Exception as exc:
        raise NoteGenerationStageError(f"Note generation failed: {exc}") from exc

    try:
        from .observability import get_db

        get_db().record_cost(
            operation="ingest",
            model=cost_info.model,
            source_type=cost_info.source_type,
            input_tokens=cost_info.input_tokens,
            output_tokens=cost_info.output_tokens,
            total_cost_usd=cost_info.total_cost_usd,
            source_url=cost_info.source_url,
        )
    except Exception as exc:
        logging.getLogger("obsidian_ai_tools.ingestion").debug(f"Failed to record cost: {exc}")

    emit("note_generated", note=note)
    emit("writing", note=note)
    try:
        file_path = write_note(
            note=note,
            vault_path=vault_path,
            inbox_folder=settings.obsidian_inbox_folder,
            target_path=existing.file_path if existing is not None else None,
        )
    except Exception as exc:
        raise VaultWriteError(f"Vault write failed: {exc}") from exc

    logging.getLogger("obsidian_ai_tools.ingestion").info(
        "Note persisted to vault",
        extra={
            "file_path": str(file_path),
            "title": note.title,
            "url": request.url,
        },
    )
    emit("note_written", note=note, file_path=file_path)
    return IngestionResult(
        provider_name=provider.name,
        prompt_version=prompt_version,
        metadata=metadata,
        note=note,
        file_path=file_path,
    )
