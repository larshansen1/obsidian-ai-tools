"""FastAPI service that exposes the kai ingest pipeline over HTTP.

Intended as a local-only daemon (127.0.0.1) consumed by the Chrome extension.
Start with: kai serve
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..config import get_settings
from ..llm import generate_note
from ..obsidian import write_note
from ..providers.factory import ProviderFactory

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    url: str
    prompt_version: str | None = None
    vault_path: str | None = None
    transcript_providers: str | None = None
    max_pages: int | None = None


class IngestResponse(BaseModel):
    status: str = "ok"
    title: str
    file_path: str
    tags: list[str]
    source_type: str


class StatusResponse(BaseModel):
    running: bool = True
    vault: str
    inbox: str
    model: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="kai",
        description="Knowledge AI Tools — local ingestion service",
        version="1.0.0",
        docs_url="/docs",
    )

    # Allow requests from the Chrome extension (and localhost dev tools).
    # The server only binds to 127.0.0.1, so wildcard origins are safe here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        settings = get_settings()
        return StatusResponse(
            vault=str(settings.obsidian_vault_path),
            inbox=settings.obsidian_inbox_folder,
            model=settings.llm_model,
        )

    @app.post("/ingest", response_model=IngestResponse)
    def ingest(req: IngestRequest) -> IngestResponse:
        settings = get_settings()
        vault_path = Path(req.vault_path) if req.vault_path else settings.obsidian_vault_path

        # Provider selection
        try:
            provider = ProviderFactory.get_provider(req.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"No provider for URL: {req.url}") from exc

        # Default prompt version per source type
        prompt_version = req.prompt_version or {
            "youtube": "youtube_v2",
            "file": "markdown_v1",
            "pdf": "pdf_v1",
        }.get(provider.name, "article_v1")

        # Provider-specific kwargs
        kwargs: dict = {}
        if provider.name == "pdf" and req.max_pages is not None:
            kwargs["max_pages"] = req.max_pages
        if provider.name == "youtube" and req.transcript_providers is not None:
            kwargs["provider_order"] = req.transcript_providers

        # Step 1: fetch content
        try:
            metadata = provider.ingest(req.url, **kwargs)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Content fetch failed: {e}") from e

        # Step 2: generate note via LLM
        try:
            note = generate_note(
                metadata=metadata,
                model=settings.llm_model,
                api_key=settings.openrouter_api_key,
                vault_path=vault_path,
                max_content_length=settings.max_transcript_length,
                prompt_version=prompt_version,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Note generation failed: {e}") from e

        # Step 3: write to vault
        try:
            file_path = write_note(
                note=note,
                vault_path=vault_path,
                inbox_folder=settings.obsidian_inbox_folder,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Vault write failed: {e}") from e

        return IngestResponse(
            title=note.title,
            file_path=str(file_path),
            tags=note.tags,
            source_type=note.source_type,
        )

    return app
