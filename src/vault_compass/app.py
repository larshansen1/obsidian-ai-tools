"""FastAPI app for Vault Compass.

Local-only daemon (127.0.0.1), started with: compass serve
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from typing import Literal

import duckdb
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from .config import CompassSettings, get_compass_settings
from .db import DB_LOCK
from .notes import UNMAPPED_TAGS_SQL
from .topic_map import TopicMapResponse, Window, build_topic_map
from .topics import TopicsError, load_topics
from .usage import log_usage
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
) -> FastAPI:
    """Build the Compass app. Used by uvicorn as a factory. `today` is a test seam."""

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

    @app.post("/usage", status_code=204)
    def usage(event: UsageEvent) -> Response:
        log_usage(current().compass_db_path, event.kind, event.name, detail=event.detail)
        return Response(status_code=204)

    return app
