"""Send a source to kai (W3): the kai client, the job queue and the API: #138."""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient

from vault_compass.app import create_app
from vault_compass.config import CompassSettings
from vault_compass.kai_ingest import (
    NOT_RUNNING,
    RESTARTED,
    IngestError,
    IngestJob,
    IngestLog,
    IngestQueue,
    KaiClient,
    KaiNote,
    KaiNotRunning,
    MonthCount,
    start_thread,
)
from vault_compass.notes import refresh_notes
from vault_compass.topics import load_topics

T0 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 8, 12, 5, 0, tzinfo=UTC)
URL = "https://example.org/paper"

Handler = Callable[[httpx.Request], httpx.Response]


def _kai(handler: Handler) -> KaiClient:
    return KaiClient("http://kai.test/", transport=httpx.MockTransport(handler))


def _up(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"running": True})


def _raise(error: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


# ---------------------------------------------------------------------------
# kai client
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "running"),
    [
        (_up, True),
        (lambda r: httpx.Response(200, json={"running": False}), False),
        (lambda r: httpx.Response(200, json=["running"]), False),
        (lambda r: httpx.Response(200, text="not json"), False),
        (lambda r: httpx.Response(404, json={"running": True}), False),
        (_raise(httpx.ConnectError("refused")), False),
    ],
)
def test_running(handler: Handler, running: bool) -> None:
    assert _kai(handler).running() is running


def test_running_asks_the_status_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"running": True})

    _kai(handler).running()

    assert seen == ["http://kai.test/status"]


def test_ingest_posts_the_url_and_the_vault(tmp_path: Path) -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), json.loads(request.content)))
        return httpx.Response(
            200, json={"status": "ok", "title": "Paper", "file_path": "/v/inbox/Paper.md"}
        )

    note = _kai(handler).ingest(URL, tmp_path)

    assert note == KaiNote("Paper", "/v/inbox/Paper.md", True)
    assert seen == [("POST", "http://kai.test/ingest", {"url": URL, "vault_path": str(tmp_path)})]


def test_ingest_of_a_source_already_in_the_vault(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "exists", "title": "P", "file_path": "/v/P.md"})

    assert _kai(handler).ingest(URL, tmp_path) == KaiNote("P", "/v/P.md", False)


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (
            lambda r: httpx.Response(409, json={"detail": {"status": "in_progress"}}),
            "kai is already working on this source. Wait for it to finish.",
        ),
        (
            lambda r: httpx.Response(422, json={"detail": "Page not found"}),
            "kai could not ingest it: Page not found",
        ),
        (
            lambda r: httpx.Response(500, json={"detail": {"message": "LLM down"}}),
            "kai could not ingest it: LLM down",
        ),
        (lambda r: httpx.Response(500, json={}), "kai could not ingest it: HTTP 500"),
        (lambda r: httpx.Response(502, json=[1]), "kai could not ingest it: HTTP 502"),
        (
            lambda r: httpx.Response(502, text="Bad gateway "),
            "kai could not ingest it: Bad gateway",
        ),
        (lambda r: httpx.Response(502, text=""), "kai could not ingest it: HTTP 502"),
        (
            lambda r: httpx.Response(200, json={"status": "ok"}),
            "kai sent an answer Compass could not read.",
        ),
        (
            lambda r: httpx.Response(200, text="<html>"),
            "kai sent an answer Compass could not read.",
        ),
        (
            _raise(httpx.ReadTimeout("slow")),
            "kai took more than 15 minutes. Check your inbox later, then try again.",
        ),
        (
            _raise(httpx.RemoteProtocolError("gone")),
            "kai serve stopped before it finished. Check your inbox, then try again.",
        ),
    ],
)
def test_ingest_failures(handler: Handler, message: str, tmp_path: Path) -> None:
    with pytest.raises(IngestError) as exc_info:
        _kai(handler).ingest(URL, tmp_path)

    assert str(exc_info.value) == message


# ---------------------------------------------------------------------------
# Job queue
# ---------------------------------------------------------------------------


class FakeKai(KaiClient):
    def __init__(self, up: bool = True, result: KaiNote | IngestError | None = None) -> None:
        super().__init__("http://kai.test")
        self.up = up
        self.result = result or KaiNote("Paper", "", True)
        self.calls: list[tuple[str, Path]] = []

    def running(self) -> bool:
        return self.up

    def ingest(self, url: str, vault: Path) -> KaiNote:
        self.calls.append((url, vault))
        if isinstance(self.result, IngestError):
            raise self.result
        return self.result


class Clock:
    def __init__(self, *times: datetime) -> None:
        self.times = list(times)

    def __call__(self) -> datetime:
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]


def _at(stamp: datetime) -> Callable[[], datetime]:
    return lambda: stamp


def _queue(
    tmp_path: Path,
    kai: KaiClient,
    *,
    run: bool = True,
    done: list[str] | None = None,
    clock: Callable[[], datetime] = lambda: T0,
) -> IngestQueue:
    later: list[Callable[[], None]] = []
    return IngestQueue(
        tmp_path / "compass.duckdb",
        tmp_path / "vault",
        kai,
        spawn=(lambda work: work()) if run else later.append,
        on_done=lambda: (done if done is not None else []).append("refresh"),
        clock=clock,
    )


def _rows(tmp_path: Path) -> int:
    db = tmp_path / "compass.duckdb"
    if not db.exists():
        return 0
    with duckdb.connect(str(db), read_only=True) as con:
        row = con.execute("SELECT COUNT(*) FROM ingest_jobs").fetchone()
    assert row is not None
    return int(row[0])


def test_kai_not_running_queues_nothing(tmp_path: Path) -> None:
    kai = FakeKai(up=False)
    queue = _queue(tmp_path, kai)

    with pytest.raises(KaiNotRunning) as exc_info:
        queue.submit(URL, "Paper", "sleep")

    assert str(exc_info.value) == NOT_RUNNING
    assert kai.calls == []
    assert _rows(tmp_path) == 0
    assert queue.log() == IngestLog(jobs=[], per_month=[])


@pytest.mark.parametrize("url", ["ftp://x", "example.org", "https://" + "x" * 1993])
def test_only_web_links_are_sent(tmp_path: Path, url: str) -> None:
    with pytest.raises(IngestError) as exc_info:
        _queue(tmp_path, FakeKai()).submit(url, "", None)

    assert str(exc_info.value) == "Only web links (http or https) can be sent to kai."
    assert _rows(tmp_path) == 0


def test_a_link_of_two_thousand_characters_is_sent(tmp_path: Path) -> None:
    url = "https://" + "x" * 1992

    assert _queue(tmp_path, FakeKai()).submit(url, "", None).url == url


def test_a_send_is_queued_then_done(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    kai = FakeKai(result=KaiNote("Paper", str(vault / "inbox" / "Paper.md"), True))
    done: list[str] = []
    queue = _queue(tmp_path, kai, done=done, clock=Clock(T0, T1))

    job = queue.submit(f"  {URL} ", " Paper ", "sleep")

    assert job == IngestJob(
        id=job.id,
        url=URL,
        title="Paper",
        topic="sleep",
        status="done",
        message="Added to the vault: Paper",
        note_path="inbox/Paper.md",
        new_note=True,
        requested_at=T0,
        finished_at=T1,
    )
    assert kai.calls == [(URL, vault)]
    assert done == ["refresh"]


def test_a_job_shows_queued_until_kai_answers(tmp_path: Path) -> None:
    queue = _queue(tmp_path, FakeKai(), run=False)

    job = queue.submit(URL, "", None)

    assert (job.status, job.message, job.title, job.finished_at) == (
        "queued",
        "Sent to kai.",
        URL,
        None,
    )
    assert queue.job(job.id) == job


def test_a_source_on_its_way_is_not_sent_twice(tmp_path: Path) -> None:
    queue = _queue(tmp_path, FakeKai(), run=False)
    queue.submit(URL, "", None)

    with pytest.raises(IngestError) as exc_info:
        queue.submit(URL, "", None)

    assert str(exc_info.value) == "This source is already on its way to kai."
    assert _rows(tmp_path) == 1


def test_a_failed_ingest_is_logged_with_kais_reason(tmp_path: Path) -> None:
    kai = FakeKai(result=IngestError("kai could not ingest it: Page not found"))
    done: list[str] = []

    job = _queue(tmp_path, kai, done=done).submit(URL, "Paper", None)

    assert (job.status, job.message, job.note_path, job.new_note) == (
        "failed",
        "kai could not ingest it: Page not found",
        None,
        False,
    )
    assert done == ["refresh"]


def test_a_source_already_in_the_vault_is_done_but_not_new(tmp_path: Path) -> None:
    kai = FakeKai(result=KaiNote("Paper", "/elsewhere/Paper.md", False))

    job = _queue(tmp_path, kai).submit(URL, "Paper", None)

    assert (job.status, job.message, job.note_path, job.new_note) == (
        "done",
        "Already in the vault: Paper",
        "Paper.md",
        False,
    )


def test_a_job_cut_off_by_a_restart_is_failed(tmp_path: Path) -> None:
    job = _queue(tmp_path, FakeKai(), run=False).submit(URL, "", None)

    after = _queue(tmp_path, FakeKai(), clock=lambda: T1).job(job.id)

    assert (after.status, after.message, after.finished_at) == ("failed", RESTARTED, T1)


def test_a_refresh_error_does_not_lose_the_result(tmp_path: Path) -> None:
    def boom() -> None:
        raise RuntimeError("refresh broke")

    queue = IngestQueue(
        tmp_path / "compass.duckdb", tmp_path, FakeKai(), spawn=lambda w: w(), on_done=boom
    )

    assert queue.submit(URL, "", None).status == "done"


def test_unknown_job(tmp_path: Path) -> None:
    queue = _queue(tmp_path, FakeKai())
    queue.submit(URL, "", None)

    with pytest.raises(IngestError) as exc_info:
        queue.job("nope")

    assert str(exc_info.value) == "No such ingest."


def test_new_notes_are_counted_per_month(tmp_path: Path) -> None:
    sends = [
        ("https://a.org", datetime(2026, 9, 30, 23, 59, tzinfo=UTC), KaiNote("P", "", True)),
        ("https://b.org", datetime(2026, 10, 1, 0, 0, tzinfo=UTC), KaiNote("P", "", True)),
        ("https://c.org", datetime(2026, 10, 2, 0, 0, tzinfo=UTC), KaiNote("P", "", True)),
        ("https://d.org", datetime(2026, 10, 3, 0, 0, tzinfo=UTC), KaiNote("P", "", False)),
        ("https://e.org", datetime(2026, 10, 4, 0, 0, tzinfo=UTC), IngestError("no")),
    ]
    for url, stamp, result in sends:
        _queue(tmp_path, FakeKai(result=result), clock=_at(stamp)).submit(url, "", None)

    log = _queue(tmp_path, FakeKai()).log()

    assert log.per_month == [
        MonthCount(month="2026-10", count=2),
        MonthCount(month="2026-09", count=1),
    ]
    assert [j.url for j in log.jobs] == [
        "https://e.org",
        "https://d.org",
        "https://c.org",
        "https://b.org",
        "https://a.org",
    ]


def test_months_shown_are_the_last_twelve(tmp_path: Path) -> None:
    for month in range(1, 14):
        stamp = datetime(2025 + month // 13, (month - 1) % 12 + 1, 5, tzinfo=UTC)
        _queue(tmp_path, FakeKai(), clock=_at(stamp)).submit(f"https://{month}.org", "", None)

    months = [m.month for m in _queue(tmp_path, FakeKai()).log().per_month]

    assert months == ["2026-01"] + [f"2025-{m:02d}" for m in range(12, 1, -1)]


def test_the_log_shows_the_twenty_newest_jobs(tmp_path: Path) -> None:
    for minute in range(21):
        stamp = datetime(2026, 10, 8, 12, minute, tzinfo=UTC)
        _queue(tmp_path, FakeKai(), clock=_at(stamp)).submit(f"https://{minute}.org", "", None)

    jobs = _queue(tmp_path, FakeKai()).log().jobs

    assert [j.url for j in jobs] == [f"https://{m}.org" for m in range(20, 0, -1)]


def test_the_log_is_empty_before_any_send(tmp_path: Path) -> None:
    duckdb.connect(str(tmp_path / "compass.duckdb")).close()

    assert _queue(tmp_path, FakeKai()).per_month() == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


TOPICS_YAML = (
    "topics:\n  sleep:\n    name: Sleep\n    tags: [sleep]\n"
    "min_notes: 1\ntrend_start: 2026-04-01\nai_exclude_folders: []\n"
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "inbox").mkdir(parents=True)
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    refresh_notes(root, root / ".kai" / "compass.duckdb", load_topics(root / ".kai/topics.yaml"))
    return root


def _api(vault: Path, monkeypatch: pytest.MonkeyPatch, kai: KaiClient) -> TestClient:
    for key in ("COMPASS_DB_PATH", "COMPASS_TOPICS_PATH", "COMPASS_KAI_URL"):
        monkeypatch.delenv(key, raising=False)
    settings = CompassSettings(obsidian_vault_path=vault, openrouter_api_key=None)
    seen: list[str] = []

    def factory(cfg: CompassSettings) -> KaiClient:
        seen.append(cfg.compass_kai_url)
        return kai

    client = TestClient(create_app(settings, kai_factory=factory, spawn=lambda w: w()))
    client.app.state.kai_urls = seen  # type: ignore[attr-defined]
    return client


def test_api_kai_not_running(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = _api(vault, monkeypatch, FakeKai(up=False))

    response = api.post("/ingests", json={"url": URL, "title": "Paper", "topic": "sleep"})

    assert response.status_code == 503
    assert response.json() == {"detail": NOT_RUNNING}
    assert api.get("/ingests").json() == {"jobs": [], "per_month": []}


def test_api_send_then_poll(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    note = vault / "inbox" / "Paper.md"

    class WritingKai(FakeKai):
        def ingest(self, url: str, vault_path: Path) -> KaiNote:
            note.write_text("---\ntitle: Paper\ntags: [sleep]\n---\nBody.\n", encoding="utf-8")
            return KaiNote("Paper", str(note), True)

    api = _api(vault, monkeypatch, WritingKai())

    sent = api.post("/ingests", json={"url": URL, "title": "Paper", "topic": "sleep"})

    assert sent.status_code == 201
    assert sent.json()["status"] == "done"
    assert sent.json()["note_path"] == "inbox/Paper.md"
    assert api.get(f"/ingests/{sent.json()['id']}").json() == sent.json()
    assert [m["count"] for m in api.get("/ingests").json()["per_month"]] == [1]
    recent = api.get("/topics/sleep").json()["recent_notes"]
    assert [n["path"] for n in recent] == ["inbox/Paper.md"]
    assert api.app.state.kai_urls == ["http://127.0.0.1:8765"]  # type: ignore[attr-defined]


def test_api_ingest_errors(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = _api(vault, monkeypatch, FakeKai())

    bad = api.post("/ingests", json={"url": "ftp://x"})
    missing = api.get("/ingests/nope")

    assert (bad.status_code, bad.json()) == (
        400,
        {"detail": "Only web links (http or https) can be sent to kai."},
    )
    assert (missing.status_code, missing.json()) == (404, {"detail": "No such ingest."})


def test_start_thread_runs_the_work_on_a_daemon_thread() -> None:
    seen: list[tuple[str, bool]] = []
    finished = threading.Event()

    def work() -> None:
        current = threading.current_thread()
        seen.append((current.name, current.daemon))
        finished.set()

    start_thread(work)

    assert finished.wait(5)
    assert seen == [("kai-ingest", True)]
