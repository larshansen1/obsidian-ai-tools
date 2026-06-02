"""FastAPI service that exposes the kai ingest pipeline over HTTP.

Intended as a local-only daemon (127.0.0.1) consumed by the Chrome extension.
Start with: kai serve
"""

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..config import get_settings
from ..ingestion import (
    ContentFetchError,
    NoteGenerationStageError,
    ProviderSelectionError,
    VaultWriteError,
    ingest_content,
)
from ..ingestion import (
    IngestionRequest as ServiceIngestionRequest,
)

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    url: str
    prompt_version: str | None = None
    vault_path: str | None = None
    transcript_providers: str | None = None
    max_pages: int | None = None
    captured_content: str | None = None
    captured_title: str | None = None


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
        from ..observability import get_db

        settings = get_settings()
        _start = time.monotonic()
        _outcome = "success"
        _error_type: str | None = None
        try:
            result = ingest_content(
                ServiceIngestionRequest(
                    url=req.url,
                    vault_path=Path(req.vault_path) if req.vault_path else None,
                    prompt_version=req.prompt_version,
                    transcript_providers=req.transcript_providers,
                    max_pages=req.max_pages,
                    captured_content=req.captured_content,
                    captured_title=req.captured_title,
                ),
                settings,
            )
        except ProviderSelectionError as exc:
            _outcome, _error_type = "error", "ProviderSelectionError"
            raise HTTPException(status_code=400, detail=f"No provider for URL: {req.url}") from exc
        except ContentFetchError as exc:
            _outcome, _error_type = "error", "ContentFetchError"
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (NoteGenerationStageError, VaultWriteError) as exc:
            _outcome, _error_type = "error", type(exc).__name__
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            try:
                get_db().record_invocation(
                    "serve:ingest", _outcome, time.monotonic() - _start, _error_type
                )
            except Exception:  # nosec B110
                pass

        return IngestResponse(
            title=result.note.title,
            file_path=str(result.file_path),
            tags=result.note.tags,
            source_type=result.note.source_type,
        )

    return app
