"""FastAPI app for Vault Compass.

Local-only daemon (127.0.0.1), started with: compass serve
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from typing import Any, Literal

import duckdb
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ai_client import ChatModel, OpenRouterModel
from .ai_cost import month_spend
from .chat import STREAM_HEADERS, chat_stream
from .chat_history import GLOBAL_KEY, load_history, save_history
from .claims import ClaimsView, build_claims_view, save_chosen_question
from .claims_run import RunStatus, run_topic_claims
from .config import CompassSettings, get_compass_settings
from .db import DB_LOCK, writable
from .note_links import plan_links
from .notes import UNMAPPED_TAGS_SQL
from .signals import SignalKind
from .source_ai import ActionBudget, OpenRouterSourceAi, SourceAi
from .topic_edit import plan_topic_tags
from .topic_map import TopicMapResponse, Window, build_topic_map
from .topic_page import TopicPageResponse, build_topic_page
from .topics import TopicsError, load_topics
from .usage import log_usage
from .vault_tools import AiVault, VaultToolError
from .vault_writes import (
    LoggedWrite,
    PendingWrite,
    WriteError,
    WriteOutcome,
    apply_write,
    cancel_write,
    plan_write,
    recent_writes,
    undo_write,
)
from .watcher import refresh_once, watch_vault

NOT_SCANNED_DETAIL = "No topic data yet. Run `compass scan` first."


class StatusResponse(BaseModel):
    # Only the running flag: no paths or config, same rule as kai (ADR 0002).
    running: bool = True


class UnmappedTag(BaseModel):
    tag: str
    notes: int


class TopicSummary(BaseModel):
    id: str
    name: str
    tags: list[str]
    note_count: int
    below_min_notes: bool


class TopicsResponse(BaseModel):
    min_notes: int
    trend_start: date
    ai_exclude_folders: list[str]
    topics: list[TopicSummary]


class UsageEvent(BaseModel):
    # ai_call is written by the server itself, never reported by the browser.
    kind: Literal["screen_view", "action"]
    name: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=1000)


class ChatRequest(BaseModel):
    # assistant-ui also sends system, tools and ids; only these are used.
    model_config = ConfigDict(extra="ignore")

    messages: list[dict[str, Any]]
    # Where the user is (C3). The topic is a topic id.
    screen: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=200)


class ClaimsRunRequest(BaseModel):
    # True after the user agreed to go over a cost limit (N3).
    approved: bool = False


class ClaimsRunResponse(BaseModel):
    status: RunStatus
    message: str | None
    estimate_usd: float | None
    view: ClaimsView


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)

    @field_validator("question")
    @classmethod
    def _trim(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("question must not be blank")
        return trimmed


class LinkRequest(BaseModel):
    evergreen: str = Field(min_length=1, max_length=500)
    notes: list[str] = Field(min_length=1, max_length=50)


class TopicTagsRequest(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=50)
    remove: list[str] = Field(default_factory=list, max_length=50)


class AiStatus(BaseModel):
    configured: bool
    model: str
    vault_name: str
    month_spend_usd: float
    monthly_limit_usd: float
    action_limit_usd: float


class ChatHistory(BaseModel):
    # The assistant-ui thread export, stored as given.
    messages: dict[str, Any] | None


_TOPIC_COUNTS_SQL = "SELECT topic, COUNT(*) FROM note_topics GROUP BY topic"


@contextmanager
def _compass_db(settings: CompassSettings) -> Iterator[duckdb.DuckDBPyConnection]:
    if not settings.compass_db_path.exists():
        raise HTTPException(status_code=404, detail=NOT_SCANNED_DETAIL)
    with DB_LOCK:
        con = duckdb.connect(str(settings.compass_db_path), read_only=True)
        try:
            yield con
        except duckdb.CatalogException:
            # A database from before topics existed: the tables are not there yet.
            raise HTTPException(status_code=404, detail=NOT_SCANNED_DETAIL) from None
        finally:
            con.close()


def create_app(
    settings: CompassSettings | None = None,
    today: Callable[[], date] = date.today,
    model_factory: Callable[[CompassSettings], ChatModel] = OpenRouterModel,
    source_ai_factory: Callable[[CompassSettings, ActionBudget], SourceAi] = OpenRouterSourceAi,
) -> FastAPI:
    """Build the Compass app. Used by uvicorn as a factory.

    `today`, `model_factory` and `source_ai_factory` are test seams.
    """

    def current() -> CompassSettings:
        return settings if settings is not None else get_compass_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Refresh before serving, then keep refreshing while the server runs (D7).
        cfg = current()
        await asyncio.to_thread(refresh_once, cfg)
        stop = asyncio.Event()
        task = asyncio.create_task(watch_vault(cfg, stop))
        try:
            yield
        finally:
            stop.set()
            await task

    app = FastAPI(title="Vault Compass", lifespan=lifespan)

    @app.get("/status")
    def status() -> StatusResponse:
        return StatusResponse()

    @app.get("/topics")
    def topics() -> TopicsResponse:
        cfg = current()
        try:
            definitions = load_topics(cfg.compass_topics_path)
        except TopicsError as e:
            raise HTTPException(status_code=500, detail=str(e)) from None
        with _compass_db(cfg) as con:
            counts = dict(con.execute(_TOPIC_COUNTS_SQL).fetchall())
        summaries = []
        for topic_id, topic in definitions.topics.items():
            count = int(counts.get(topic_id, 0))
            summaries.append(
                TopicSummary(
                    id=topic_id,
                    name=topic.name,
                    tags=topic.tags,
                    note_count=count,
                    below_min_notes=count < definitions.min_notes,
                )
            )
        return TopicsResponse(
            min_notes=definitions.min_notes,
            trend_start=definitions.trend_start,
            ai_exclude_folders=definitions.ai_exclude_folders,
            topics=summaries,
        )

    @app.get("/topic-map")
    def topic_map(window: Window = "30") -> TopicMapResponse:
        cfg = current()
        try:
            definitions = load_topics(cfg.compass_topics_path)
        except TopicsError as e:
            raise HTTPException(status_code=500, detail=str(e)) from None
        with _compass_db(cfg) as con:
            return build_topic_map(
                con,
                definitions,
                today=today(),
                window=window,
                inbox_folder=cfg.obsidian_inbox_folder,
            )

    @app.get("/topics/unmapped")
    def unmapped_tags() -> list[UnmappedTag]:
        with _compass_db(current()) as con:
            rows = con.execute(UNMAPPED_TAGS_SQL).fetchall()
        return [UnmappedTag(tag=tag, notes=notes) for tag, notes in rows]

    @app.get("/topics/{topic_id}")
    def topic_page(
        topic_id: str, window: Window = "30", signal: SignalKind | None = None
    ) -> TopicPageResponse:
        cfg = current()
        try:
            definitions = load_topics(cfg.compass_topics_path)
        except TopicsError as e:
            raise HTTPException(status_code=500, detail=str(e)) from None
        if topic_id not in definitions.topics:
            raise HTTPException(status_code=404, detail=f"Unknown topic: {topic_id}")
        with _compass_db(cfg) as con:
            return build_topic_page(
                con, definitions, topic_id, today=today(), window=window, signal=signal
            )

    def _claims_view(cfg: CompassSettings, topic_id: str) -> ClaimsView:
        vault = _vault(cfg)
        if topic_id not in vault.definitions.topics:
            raise HTTPException(status_code=404, detail=f"Unknown topic: {topic_id}")
        with _compass_db(cfg) as con:
            return build_claims_view(con, vault.definitions, topic_id)

    @app.get("/topics/{topic_id}/coverage")
    def topic_coverage(topic_id: str) -> dict[str, Any]:
        """Coverage bars for the topic page: rules and cached types only, no model calls."""
        try:
            summary: dict[str, Any] = _vault(current()).coverage_gaps(topic_id)
        except VaultToolError as e:
            status = 404 if str(e).startswith("Unknown topic") else 409
            raise HTTPException(status_code=status, detail=str(e)) from None
        return summary

    @app.get("/topics/{topic_id}/claims")
    def topic_claims(topic_id: str) -> ClaimsView:
        return _claims_view(current(), topic_id)

    @app.post("/topics/{topic_id}/claims/run")
    async def run_claims(topic_id: str, request: ClaimsRunRequest) -> ClaimsRunResponse:
        cfg = current()
        await asyncio.to_thread(_claims_view, cfg, topic_id)  # 404s before any model work
        outcome = await run_topic_claims(
            cfg, _vault(cfg), model_factory, topic_id, approved=request.approved
        )
        return ClaimsRunResponse(
            status=outcome.status,
            message=outcome.message,
            estimate_usd=outcome.estimate_usd,
            view=await asyncio.to_thread(_claims_view, cfg, topic_id),
        )

    @app.put("/topics/{topic_id}/claims/question")
    def put_claims_question(topic_id: str, request: QuestionRequest) -> ClaimsView:
        cfg = current()
        _claims_view(cfg, topic_id)
        with writable(cfg.compass_db_path) as con:
            save_chosen_question(con, topic_id, request.question)
        return _claims_view(cfg, topic_id)

    def _vault(cfg: CompassSettings) -> AiVault:
        try:
            return AiVault.from_settings(cfg)
        except TopicsError as e:
            raise HTTPException(status_code=500, detail=str(e)) from None

    def _thread_key(cfg: CompassSettings, topic: str | None) -> str:
        if topic is None:
            return GLOBAL_KEY
        if topic not in _vault(cfg).definitions.topics:
            raise HTTPException(status_code=404, detail=f"Unknown topic: {topic}")
        return topic

    @app.get("/ai/status")
    def ai_status() -> AiStatus:
        cfg = current()
        return AiStatus(
            configured=bool(cfg.openrouter_api_key),
            model=cfg.llm_model,
            vault_name=cfg.obsidian_vault_path.name,
            month_spend_usd=month_spend(cfg.compass_db_path),
            monthly_limit_usd=cfg.compass_ai_monthly_limit_usd,
            action_limit_usd=cfg.compass_ai_action_limit_usd,
        )

    @app.post("/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        cfg = current()
        return StreamingResponse(
            chat_stream(
                settings=cfg,
                vault=_vault(cfg),
                model_factory=model_factory,
                messages=request.messages,
                screen=request.screen,
                topic=request.topic,
                today=today(),
                source_ai_factory=source_ai_factory,
            ),
            media_type="text/event-stream",
            headers=STREAM_HEADERS,
        )

    @app.get("/chat/history")
    def get_chat_history(topic: str | None = None) -> ChatHistory:
        cfg = current()
        return ChatHistory(messages=load_history(cfg.compass_db_path, _thread_key(cfg, topic)))

    @app.put("/chat/history", status_code=204)
    def put_chat_history(history: ChatHistory, topic: str | None = None) -> Response:
        cfg = current()
        save_history(cfg.compass_db_path, _thread_key(cfg, topic), history.messages)
        return Response(status_code=204)

    # --- Write-back (W1, W2, W4, W5): plan, then apply on a click, then undo ---

    @app.post("/writes/links")
    def plan_link_write(request: LinkRequest) -> PendingWrite:
        cfg = current()
        vault = _vault(cfg)
        try:
            edits, summary = plan_links(
                cfg.obsidian_vault_path,
                cfg.compass_db_path,
                vault.excluded,
                request.evergreen,
                request.notes,
            )
        except WriteError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return plan_write(cfg.compass_db_path, "link_notes", summary, edits)

    @app.post("/topics/{topic_id}/tags")
    def plan_topic_write(topic_id: str, request: TopicTagsRequest) -> PendingWrite:
        cfg = current()
        try:
            edit, summary = plan_topic_tags(
                cfg.compass_topics_path, topic_id, request.add, request.remove
            )
        except WriteError as e:
            status = 404 if str(e).startswith("Unknown topic") else 400
            raise HTTPException(status_code=status, detail=str(e)) from None
        return plan_write(cfg.compass_db_path, "edit_topic", summary, [edit])

    @app.post("/writes/{write_id}/apply")
    def apply(write_id: str) -> WriteOutcome:
        cfg = current()
        try:
            outcome = apply_write(cfg.compass_db_path, write_id)
        except WriteError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        if outcome.status == "written":
            refresh_once(cfg)  # so the next read shows the new links and tags
        return outcome

    @app.delete("/writes/{write_id}", status_code=204)
    def cancel(write_id: str) -> Response:
        cancel_write(current().compass_db_path, write_id)
        return Response(status_code=204)

    @app.get("/writes")
    def writes() -> list[LoggedWrite]:
        return recent_writes(current().compass_db_path)

    @app.post("/writes/{write_id}/undo")
    def undo(write_id: str) -> WriteOutcome:
        cfg = current()
        try:
            outcome = undo_write(cfg.compass_db_path, write_id)
        except WriteError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        if outcome.status == "undone":
            refresh_once(cfg)
        return outcome

    @app.post("/usage", status_code=204)
    def usage(event: UsageEvent) -> Response:
        log_usage(current().compass_db_path, event.kind, event.name, detail=event.detail)
        return Response(status_code=204)

    return app
