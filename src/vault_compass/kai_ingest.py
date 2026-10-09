"""Send a source to kai (W3): POST to kai serve's /ingest, tracked as a job.

kai's /ingest answers only when the note is written, which can take minutes, so
each send runs in the background and the browser polls the job: queued, then
done or failed. Every send is logged; new notes are counted per month, the
"sources ingested from app suggestions" success measure. An ingest is not
undoable here: kai wrote the note, and it is removed in Obsidian if unwanted.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import httpx
from pydantic import BaseModel

from .db import readonly, writable

STATUS_TIMEOUT_S = 3.0
INGEST_TIMEOUT_S = 900.0
MAX_URL = 2000
RECENT_JOBS = 20
MONTHS_SHOWN = 12

NOT_RUNNING = "kai serve is not running. Start it with `kai serve`, then click Ingest again."
RESTARTED = "Compass restarted before kai answered. Check your inbox before trying again."

logger = logging.getLogger(__name__)

_DDL = """
    CREATE TABLE IF NOT EXISTS ingest_jobs (
        id VARCHAR PRIMARY KEY,
        url VARCHAR NOT NULL,
        title VARCHAR NOT NULL,
        topic VARCHAR,
        status VARCHAR NOT NULL,
        message VARCHAR NOT NULL,
        note_path VARCHAR,
        new_note BOOLEAN NOT NULL,
        requested_at TIMESTAMP NOT NULL,
        finished_at TIMESTAMP
    )
"""

_SELECT_JOB = (
    "SELECT id, url, title, topic, status, message, note_path, new_note, requested_at, finished_at "
    "FROM ingest_jobs WHERE id = ?"
)
_SELECT_RECENT = (
    "SELECT id, url, title, topic, status, message, note_path, new_note, requested_at, finished_at "
    "FROM ingest_jobs ORDER BY requested_at DESC, id LIMIT ?"
)

JobStatus = Literal["queued", "done", "failed"]


class IngestError(Exception):
    """A source cannot be sent, or kai could not ingest it. The message is user-facing."""


class KaiNotRunning(IngestError):
    """kai serve did not answer, so nothing was queued."""


class IngestJob(BaseModel):
    id: str
    url: str
    title: str
    topic: str | None
    status: JobStatus
    message: str
    note_path: str | None
    new_note: bool
    requested_at: datetime
    finished_at: datetime | None


class MonthCount(BaseModel):
    month: str
    count: int


class IngestLog(BaseModel):
    jobs: list[IngestJob]
    # New notes from app suggestions per month, newest first.
    per_month: list[MonthCount]


@dataclass(frozen=True)
class KaiNote:
    title: str
    file_path: str
    # False when kai found the source already in the vault.
    new: bool


def _detail(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail) if detail else f"HTTP {response.status_code}"


class KaiClient:
    """kai serve's HTTP API. `transport` is a test seam."""

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=timeout, transport=self._transport)

    def running(self) -> bool:
        try:
            with self._client(STATUS_TIMEOUT_S) as client:
                response = client.get("/status")
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        try:
            return response.json().get("running") is True
        except (ValueError, AttributeError):
            return False

    def ingest(self, url: str, vault: Path) -> KaiNote:
        try:
            with self._client(INGEST_TIMEOUT_S) as client:
                response = client.post("/ingest", json={"url": url, "vault_path": str(vault)})
        except httpx.TimeoutException:
            raise IngestError(
                "kai took more than 15 minutes. Check your inbox later, then try again."
            ) from None
        except httpx.HTTPError:
            raise IngestError(
                "kai serve stopped before it finished. Check your inbox, then try again."
            ) from None
        if response.status_code == 409:
            raise IngestError("kai is already working on this source. Wait for it to finish.")
        if response.status_code != 200:
            raise IngestError(f"kai could not ingest it: {_detail(response)}")
        try:
            body = response.json()
            return KaiNote(str(body["title"]), str(body["file_path"]), body["status"] != "exists")
        except (ValueError, KeyError, TypeError):
            raise IngestError("kai sent an answer Compass could not read.") from None


def _utc(value: datetime | None) -> datetime | None:
    # Stored as naive UTC; sent with its zone so the browser shows local time.
    return None if value is None else value.replace(tzinfo=UTC)


def _job(row: tuple[Any, ...]) -> IngestJob:
    i, url, title, topic, status, message, note, new, requested, finished = row
    return IngestJob(
        id=i,
        url=url,
        title=title,
        topic=topic,
        status=status,
        message=message,
        note_path=note,
        new_note=bool(new),
        requested_at=requested.replace(tzinfo=UTC),
        finished_at=_utc(finished),
    )


def _relative(vault: Path, file_path: str) -> str:
    path = Path(file_path)
    try:
        return path.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return path.name


def start_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="kai-ingest", daemon=True).start()


class IngestQueue:
    """Sends sources to kai one job at a time per source, and keeps the log.

    `spawn` runs a job (a daemon thread by default; tests run it inline).
    `on_done` runs after each finished job, so the topic data picks up the new note.
    `clock` gives the time in UTC.
    """

    def __init__(
        self,
        db_path: Path,
        vault: Path,
        kai: KaiClient,
        spawn: Callable[[Callable[[], None]], None] = start_thread,
        on_done: Callable[[], None] = lambda: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db_path = db_path
        self.vault = vault
        self.kai = kai
        self._spawn = spawn
        self._on_done = on_done
        self._clock = clock
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def _now(self) -> datetime:
        return self._clock().replace(tzinfo=None)

    def submit(self, url: str, title: str, topic: str | None) -> IngestJob:
        """Queue a send after checking kai is up. Raises before queueing anything."""
        url = url.strip()
        if not url.startswith(("http://", "https://")) or len(url) > MAX_URL:
            raise IngestError("Only web links (http or https) can be sent to kai.")
        if not self.kai.running():
            raise KaiNotRunning(NOT_RUNNING)
        job_id = uuid.uuid4().hex
        with self._lock, writable(self.db_path) as con:
            con.execute(_DDL)
            self._expire(con)
            busy = con.execute(
                "SELECT 1 FROM ingest_jobs WHERE url = ? AND status = 'queued'", [url]
            ).fetchone()
            if busy is not None:
                raise IngestError("This source is already on its way to kai.")
            con.execute(
                "INSERT INTO ingest_jobs (id, url, title, topic, status, message, note_path, "
                "new_note, requested_at, finished_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, NULL, false, ?, NULL)",
                [job_id, url, title.strip() or url, topic, "Sent to kai.", self._now()],
            )
            self._active.add(job_id)
        self._spawn(lambda: self._run(job_id, url))
        return self.job(job_id)

    def _run(self, job_id: str, url: str) -> None:
        try:
            note = self.kai.ingest(url, self.vault)
        except IngestError as err:
            self._finish(job_id, "failed", str(err), None, new=False)
        else:
            label = "Added to the vault" if note.new else "Already in the vault"
            path = _relative(self.vault, note.file_path)
            self._finish(job_id, "done", f"{label}: {note.title}", path, new=note.new)
        try:
            self._on_done()
        except Exception:
            logger.exception("Refresh after ingest %s failed", job_id)

    def _finish(
        self, job_id: str, status: JobStatus, message: str, note: str | None, *, new: bool
    ) -> None:
        with self._lock, writable(self.db_path) as con:
            con.execute(
                "UPDATE ingest_jobs SET status = ?, message = ?, note_path = ?, new_note = ?, "
                "finished_at = ? WHERE id = ?",
                [status, message, note, new, self._now(), job_id],
            )
            self._active.discard(job_id)

    def _expire(self, con: duckdb.DuckDBPyConnection) -> None:
        """A queued job no thread here is running was cut off by a restart: mark it failed."""
        rows = con.execute("SELECT id FROM ingest_jobs WHERE status = 'queued'").fetchall()
        queued = [i for (i,) in rows]
        for job_id in queued:
            if job_id not in self._active:
                con.execute(
                    "UPDATE ingest_jobs SET status = 'failed', message = ?, finished_at = ? "
                    "WHERE id = ?",
                    [RESTARTED, self._now(), job_id],
                )

    def _rows(self, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
        if not self.db_path.exists():
            return []
        with self._lock, writable(self.db_path) as con:
            con.execute(_DDL)
            self._expire(con)
            return con.execute(sql, params).fetchall()

    def job(self, job_id: str) -> IngestJob:
        rows = self._rows(_SELECT_JOB, [job_id])
        if not rows:
            raise IngestError("No such ingest.")
        return _job(rows[0])

    def log(self) -> IngestLog:
        jobs = self._rows(
            _SELECT_RECENT,
            [RECENT_JOBS],
        )
        return IngestLog(jobs=[_job(r) for r in jobs], per_month=self.per_month())

    def per_month(self) -> list[MonthCount]:
        if not self.db_path.exists():
            return []
        with readonly(self.db_path) as con:
            try:
                rows = con.execute(
                    "SELECT strftime(requested_at, '%Y-%m') AS month, COUNT(*) FROM ingest_jobs "
                    "WHERE status = 'done' AND new_note GROUP BY month ORDER BY month DESC "
                    "LIMIT ?",
                    [MONTHS_SHOWN],
                ).fetchall()
            except duckdb.CatalogException:
                return []
        return [MonthCount(month=m, count=c) for m, c in rows]
