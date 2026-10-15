"""FastAPI service that exposes the kai ingest pipeline over HTTP.

Intended as a local-only daemon (127.0.0.1) consumed by the Chrome extension.
Start with: kai serve
"""

import logging
import math
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from ..config import get_settings
from ..dedup import ExistingNote, find_note_by_source, normalize_source_url
from ..ingestion import (
    ContentFetchError,
    IngestionResult,
    NoteGenerationStageError,
    ProviderSelectionError,
    VaultWriteError,
    ingest_content,
)
from ..ingestion import (
    IngestionRequest as ServiceIngestionRequest,
)
from ..obsidian import build_obsidian_url

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    url: str
    prompt_version: str | None = None
    vault_path: str | None = None
    transcript_providers: str | None = None
    max_pages: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="before")
    @classmethod
    def _mask_non_finite_floats(cls, data: Any) -> Any:
        """Mask non-finite floats so FastAPI's validation-error body stays
        JSON-serializable (json.dumps(allow_nan=False) would otherwise 500)."""
        if not isinstance(data, dict):
            return data
        return {
            k: "NaN" if isinstance(v, float) and not math.isfinite(v) else v
            for k, v in data.items()
        }

    captured_content: str | None = None
    captured_title: str | None = None
    captured_author: str | None = None
    captured_date: str | None = None
    captured_html: str | None = None
    update: bool = False


class IngestResponse(BaseModel):
    status: str = "ok"
    title: str
    file_path: str
    tags: list[str]
    source_type: str
    obsidian_url: str | None = None


class LookupResponse(BaseModel):
    exists: bool = False
    in_progress: bool = False
    started_at: str | None = None
    elapsed_seconds: float | None = None
    title: str | None = None
    file_path: str | None = None
    tags: list[str] = []
    source_type: str | None = None
    obsidian_url: str | None = None


class StatusResponse(BaseModel):
    running: bool = True


# ---------------------------------------------------------------------------
# In-flight ingest registry
# ---------------------------------------------------------------------------

# Normalized source URL -> UTC start time of the running ingest. Entries are
# added before the pipeline runs and removed in a finally block, so a reclick
# of the same URL never starts a second pipeline while one is already running.
# FastAPI runs sync handlers in the threadpool, so all access is lock-guarded.
_in_flight: dict[str, datetime] = {}
_in_flight_lock = Lock()

_server_logger = logging.getLogger("obsidian_ai_tools.server")


def _utcnow() -> datetime:
    """Injectable wall clock so tests can freeze registry timestamps."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _service_request(req: IngestRequest) -> ServiceIngestionRequest:
    """Translate the HTTP request body into the ingestion service request."""
    return ServiceIngestionRequest(
        url=req.url,
        vault_path=Path(req.vault_path) if req.vault_path else None,
        prompt_version=req.prompt_version,
        transcript_providers=req.transcript_providers,
        max_pages=req.max_pages,
        captured_content=req.captured_content,
        captured_title=req.captured_title,
        captured_author=req.captured_author,
        captured_date=req.captured_date,
        captured_html=req.captured_html,
        update=req.update,
    )


def _record_ingest_finished(url: str, outcome: str, elapsed: float, error_type: str | None) -> None:
    """Log the finished ingest and record it; a metrics failure never fails the request."""
    from ..observability import get_db

    _server_logger.info(
        "Server ingest finished",
        extra={
            "url": url,
            "outcome": outcome,
            "duration_seconds": elapsed,
            "error_type": error_type,
        },
    )
    try:
        get_db().record_invocation("serve:ingest", outcome, elapsed, error_type)
    except Exception:  # nosec B110
        pass


def _ingest_response(
    req: IngestRequest, result: IngestionResult | ExistingNote, default_vault: Path
) -> IngestResponse:
    """Build the reply for a new note or for one that already existed."""
    vault_path = Path(req.vault_path) if req.vault_path else default_vault
    try:
        obsidian_url: str | None = build_obsidian_url(vault_path, result.file_path)
    except ValueError:
        obsidian_url = None

    if isinstance(result, ExistingNote):
        return IngestResponse(
            status="exists",
            title=result.title,
            file_path=str(result.file_path),
            tags=result.tags,
            source_type=result.source_type or "unknown",
            obsidian_url=obsidian_url,
        )

    return IngestResponse(
        title=result.note.title,
        file_path=str(result.file_path),
        tags=result.note.tags,
        source_type=result.note.source_type,
        obsidian_url=obsidian_url,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="kai",
        description="Knowledge AI Tools — local ingestion service",
        version="1.0.0",
        docs_url="/docs",
    )

    # Only the Chrome extension may call this API from a browser context.
    # A wildcard would let any web page fire drive-by /ingest requests
    # (CORS restricts browser pages, not network reachability — binding to
    # 127.0.0.1 does not protect against JS running in the local browser).
    # Regex instead of an exact ID because unpacked extension IDs differ
    # per machine. Non-browser clients (curl, scripts) are unaffected.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://.*",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        return StatusResponse()

    # Read-only duplicate check for the extension popup: it runs on popup open,
    # so it must stay a frontmatter scan — no fetch, no LLM, no vault write.
    # Declared sync so FastAPI runs the filesystem walk in the threadpool
    # instead of blocking the event loop.
    @app.get("/lookup", response_model=LookupResponse)
    def lookup(url: str, vault_path: str | None = None) -> LookupResponse:
        settings = get_settings()
        key = normalize_source_url(url)
        with _in_flight_lock:
            started_at = _in_flight.get(key)
        if started_at is not None:
            return LookupResponse(
                exists=False,
                in_progress=True,
                started_at=started_at.isoformat(),
                elapsed_seconds=(_utcnow() - started_at).total_seconds(),
            )
        vault = Path(vault_path) if vault_path else settings.obsidian_vault_path
        existing = find_note_by_source(vault, url)
        if existing is None:
            return LookupResponse(exists=False)

        try:
            obsidian_url: str | None = build_obsidian_url(vault, existing.file_path)
        except ValueError:
            obsidian_url = None

        return LookupResponse(
            exists=True,
            title=existing.title,
            file_path=str(existing.file_path),
            tags=existing.tags,
            source_type=existing.source_type or "unknown",
            obsidian_url=obsidian_url,
        )

    @app.post("/ingest", response_model=IngestResponse)
    def ingest(req: IngestRequest) -> IngestResponse:
        # setup_logging() binds one trace_id at server start; without a fresh
        # one per request every ingest of a session shares it. The context
        # manager restores the old value on exit so no ID leaks to the next
        # request handled by the same threadpool worker.
        with structlog.contextvars.bound_contextvars(trace_id=str(uuid.uuid4())):
            return _ingest(req)

    def _ingest(req: IngestRequest) -> IngestResponse:
        settings = get_settings()
        key = normalize_source_url(req.url)
        with _in_flight_lock:
            if key in _in_flight:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "status": "in_progress",
                        "message": "Ingest already in progress for this URL",
                    },
                )
            _in_flight[key] = _utcnow()
        _server_logger.info("Server ingest started", extra={"url": req.url})

        _start = time.monotonic()
        _outcome = "success"
        _error_type: str | None = None
        try:
            result = ingest_content(_service_request(req), settings)
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
            with _in_flight_lock:
                _in_flight.pop(key, None)
            _record_ingest_finished(req.url, _outcome, time.monotonic() - _start, _error_type)

        return _ingest_response(req, result, settings.obsidian_vault_path)

    return app
