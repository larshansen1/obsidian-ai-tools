"""Tests for incremental refresh, the vault watcher and the usage log (D6, D7)."""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient
from watchfiles import Change

from obsidian_ai_tools._vault_store import VaultStore
from vault_compass import watcher
from vault_compass.app import create_app
from vault_compass.config import CompassSettings
from vault_compass.notes import (
    RefreshStats,
    refresh_notes,
    refresh_notes_incremental,
)
from vault_compass.topics import TopicsFile
from vault_compass.usage import log_usage


def _write(vault: Path, rel: str, text: str, mtime_ns: int | None = None) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def _note(title: str, tags: str = "[]", body: str = "") -> str:
    return f"---\ntitle: {title}\ntags: {tags}\n---\n{body}\n"


def _rows(db: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _topics() -> TopicsFile:
    return TopicsFile.model_validate({"topics": {"ai": {"name": "AI", "tags": ["ai"]}}})


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault, vault / ".kai" / "compass.duckdb"


T0 = 1_700_000_000_000_000_000
T1 = T0 + 5_000_000_000


# ---------------------------------------------------------------------------
# Incremental refresh
# ---------------------------------------------------------------------------


def test_first_incremental_refresh_is_a_full_build(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A", "[ai]"), T0)

    stats = refresh_notes_incremental(vault, db, _topics())

    assert stats == RefreshStats(note_count=1, added=1, updated=0, removed=0, full=True)
    assert _rows(db, "SELECT path, title FROM notes") == [("a.md", "A")]
    assert _rows(db, "SELECT path, topic FROM note_topics") == [("a.md", "ai")]


def test_unchanged_vault_parses_nothing(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    _write(vault, "b.md", _note("B"), T0)
    refresh_notes_incremental(vault, db)

    with patch.object(VaultStore, "parse_frontmatter", wraps=VaultStore.parse_frontmatter) as spy:
        stats = refresh_notes_incremental(vault, db)

    assert stats == RefreshStats(note_count=2, added=0, updated=0, removed=0, full=False)
    spy.assert_not_called()


def test_new_file_is_the_only_file_parsed(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    refresh_notes_incremental(vault, db)
    _write(vault, "b.md", _note("B", "[x]"), T1)

    with patch.object(VaultStore, "parse_frontmatter", wraps=VaultStore.parse_frontmatter) as spy:
        stats = refresh_notes_incremental(vault, db)

    assert stats == RefreshStats(note_count=2, added=1, updated=0, removed=0, full=False)
    spy.assert_called_once_with(vault / "b.md")
    assert _rows(db, "SELECT path FROM notes ORDER BY path") == [("a.md",), ("b.md",)]
    assert _rows(db, "SELECT path, tag FROM note_tags") == [("b.md", "x")]


def test_changed_file_is_replaced_not_duplicated(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("Old", "[x, y]"), T0)
    refresh_notes_incremental(vault, db)
    _write(vault, "a.md", _note("New", "[z]"), T1)

    stats = refresh_notes_incremental(vault, db)

    assert stats == RefreshStats(note_count=1, added=0, updated=1, removed=0, full=False)
    assert _rows(db, "SELECT path, title FROM notes") == [("a.md", "New")]
    assert _rows(db, "SELECT path, tag FROM note_tags") == [("a.md", "z")]
    assert _rows(db, "SELECT path, mtime_ns FROM note_files") == [("a.md", T1)]


def test_deleted_file_is_removed_everywhere(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A", "[x]", "[[b]]"), T0)
    _write(vault, "b.md", _note("B", "[x]"), T0)
    refresh_notes_incremental(vault, db)
    (vault / "b.md").unlink()

    stats = refresh_notes_incremental(vault, db)

    assert stats == RefreshStats(note_count=1, added=0, updated=0, removed=1, full=False)
    assert _rows(db, "SELECT path FROM notes") == [("a.md",)]
    assert _rows(db, "SELECT path FROM note_tags") == [("a.md",)]
    assert _rows(db, "SELECT path FROM note_files") == [("a.md",)]
    # The link to the deleted note is now unresolved.
    assert _rows(db, "SELECT source_path, target, target_path, is_resolved FROM links") == [
        ("a.md", "b", None, False)
    ]


def test_new_note_resolves_an_old_link_without_rereading_the_old_note(
    paths: tuple[Path, Path],
) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A", body="see [[b]]"), T0)
    refresh_notes_incremental(vault, db)
    assert _rows(db, "SELECT is_resolved FROM links") == [(False,)]
    _write(vault, "b.md", _note("B"), T1)

    refresh_notes_incremental(vault, db)

    assert _rows(db, "SELECT source_path, target, target_path, is_resolved FROM links") == [
        ("a.md", "b", "b.md", True)
    ]


def test_new_note_creates_a_duplicate_pair_with_an_old_note(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", "---\ntitle: A\nsource_url: https://x.test/p\n---\n", T0)
    refresh_notes_incremental(vault, db)
    _write(vault, "b.md", "---\ntitle: B\nsource_url: https://x.test/p\n---\n", T1)

    refresh_notes_incremental(vault, db)

    assert _rows(db, "SELECT path_a, path_b FROM duplicates") == [("a.md", "b.md")]


def test_incremental_result_equals_a_full_rebuild(paths: tuple[Path, Path], tmp_path: Path) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A", "[ai]", "[[b]] [[nope]]"), T0)
    _write(vault, "b.md", _note("B", "[ai, x]"), T0)
    refresh_notes_incremental(vault, db, _topics())
    _write(vault, "c.md", _note("C", "[x]", "[[a]]"), T1)
    _write(vault, "b.md", _note("B2", "[y]", "[[c]]"), T1)
    (vault / "a.md").unlink()
    refresh_notes_incremental(vault, db, _topics())

    fresh = tmp_path / "fresh.duckdb"
    refresh_notes(vault, fresh, _topics())

    for sql in (
        "SELECT * FROM notes ORDER BY path",
        "SELECT * FROM note_tags ORDER BY path, tag",
        "SELECT * FROM links ORDER BY source_path, target",
        "SELECT * FROM duplicates ORDER BY path_a, path_b",
        "SELECT * FROM note_topics ORDER BY path, topic",
        "SELECT * FROM note_files ORDER BY path",
    ):
        assert _rows(db, sql) == _rows(fresh, sql), sql


def test_hidden_folders_are_ignored(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    _write(vault, ".obsidian/x.md", _note("X"), T0)

    stats = refresh_notes_incremental(vault, db)

    assert stats.note_count == 1


def test_database_from_before_mtimes_were_tracked_is_rebuilt(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    refresh_notes(vault, db)
    con = duckdb.connect(str(db))
    con.execute("DROP TABLE note_files")
    con.close()

    stats = refresh_notes_incremental(vault, db)

    assert stats == RefreshStats(note_count=1, added=1, updated=0, removed=0, full=True)
    assert _rows(db, "SELECT path, mtime_ns FROM note_files") == [("a.md", T0)]


def test_topics_none_keeps_existing_topic_tables(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A", "[ai]"), T0)
    refresh_notes_incremental(vault, db, _topics())
    _write(vault, "b.md", _note("B", "[ai]"), T1)

    refresh_notes_incremental(vault, db, None)

    assert _rows(db, "SELECT path, topic FROM note_topics ORDER BY path") == [
        ("a.md", "ai"),
        ("b.md", "ai"),
    ]


def test_topics_given_replaces_topic_tags(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A", "[ai, ml]"), T0)
    refresh_notes_incremental(vault, db, _topics())
    new = TopicsFile.model_validate({"topics": {"ml": {"name": "ML", "tags": ["ml"]}}})

    refresh_notes_incremental(vault, db, new)

    assert _rows(db, "SELECT topic, tag FROM topic_tags") == [("ml", "ml")]
    assert _rows(db, "SELECT path, topic FROM note_topics") == [("a.md", "ml")]


def test_failed_incremental_refresh_changes_nothing(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    refresh_notes_incremental(vault, db)
    _write(vault, "b.md", _note("B"), T1)

    with (
        patch("vault_compass.notes._rebuild_relations", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        refresh_notes_incremental(vault, db)

    assert _rows(db, "SELECT path FROM notes") == [("a.md",)]
    assert _rows(db, "SELECT path FROM note_files") == [("a.md",)]


def test_full_scan_records_mtimes_and_link_targets(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A", body="[[b]] [[c]]"), T0)

    refresh_notes(vault, db)

    assert _rows(db, "SELECT path, mtime_ns, list_sort(link_targets) FROM note_files") == [
        ("a.md", T0, ["b", "c"])
    ]


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


def _settings(vault: Path) -> CompassSettings:
    return CompassSettings(obsidian_vault_path=vault)  # type: ignore[call-arg]


def test_relevant_change_accepts_visible_markdown(tmp_path: Path) -> None:
    topics = tmp_path / ".kai" / "topics.yaml"
    assert watcher.is_relevant_change(tmp_path, topics, str(tmp_path / "notes" / "a.md")) is True


@pytest.mark.parametrize(
    "rel",
    [".obsidian/a.md", "notes/.trash/a.md", "notes/a.txt", ".kai/compass.duckdb"],
)
def test_relevant_change_rejects_hidden_and_non_markdown(tmp_path: Path, rel: str) -> None:
    topics = tmp_path / ".kai" / "topics.yaml"
    assert watcher.is_relevant_change(tmp_path, topics, str(tmp_path / rel)) is False


def test_relevant_change_accepts_the_topics_file(tmp_path: Path) -> None:
    topics = tmp_path / ".kai" / "topics.yaml"
    assert watcher.is_relevant_change(tmp_path, topics, str(topics)) is True


def test_relevant_change_rejects_paths_outside_the_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    assert (
        watcher.is_relevant_change(vault, vault / ".kai" / "t.yaml", str(tmp_path / "x.md"))
        is False
    )


def test_refresh_once_logs_counts_and_runs_incremental(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    settings = _settings(vault)

    with (
        caplog.at_level(logging.INFO, logger="vault_compass.watcher"),
        patch("vault_compass.watcher.time.perf_counter", side_effect=[10.0, 10.25]),
    ):
        watcher.refresh_once(settings)

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (
            logging.WARNING,
            "topics not loaded, topic tables left as they are: "
            + caplog.records[0].getMessage().split(": ", 1)[1],
        ),
        (
            logging.INFO,
            "compass refresh: 1 notes (1 added, 0 updated, 0 removed, full=True) in 0.25s",
        ),
    ]
    assert _rows(settings.compass_db_path, "SELECT path FROM notes") == [("a.md",)]


def test_refresh_once_swallows_and_logs_failures(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    vault, _ = paths
    with (
        patch("vault_compass.watcher.refresh_notes_incremental", side_effect=OSError("locked")),
        caplog.at_level(logging.ERROR, logger="vault_compass.watcher"),
    ):
        watcher.refresh_once(_settings(vault))

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.ERROR, "compass refresh failed")
    ]


def test_watch_vault_refreshes_after_each_batch_and_stops(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    changed = {(Change.added, str(tmp_path / "a.md"))}
    calls: list[CompassSettings] = []

    async def fake_awatch(*args, **kwargs):  # type: ignore[no-untyped-def]
        fake_awatch.args, fake_awatch.kwargs = args, kwargs
        yield changed
        yield changed

    async def run() -> None:
        with (
            patch("vault_compass.watcher.awatch", fake_awatch),
            patch("vault_compass.watcher.refresh_once", side_effect=calls.append),
        ):
            await watcher.watch_vault(settings, asyncio.Event())

    asyncio.run(run())

    assert calls == [settings, settings]
    assert fake_awatch.args == (settings.obsidian_vault_path, settings.obsidian_vault_path)
    assert fake_awatch.kwargs["debounce"] == 500
    keep = fake_awatch.kwargs["watch_filter"]
    assert keep(Change.added, str(tmp_path / "a.md")) is True
    assert keep(Change.added, str(tmp_path / ".obsidian" / "a.md")) is False


def test_server_startup_refreshes_then_watches(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    settings = _settings(vault)

    async def idle_watch(cfg: CompassSettings, stop: asyncio.Event) -> None:
        await stop.wait()

    with patch("vault_compass.app.watch_vault", idle_watch):
        with TestClient(create_app(settings)) as client:
            assert client.get("/status").status_code == 200
            assert _rows(db, "SELECT path FROM notes") == [("a.md",)]


# ---------------------------------------------------------------------------
# Usage log
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 6, 12, 30, 0)


def test_log_usage_writes_one_exact_row(paths: tuple[Path, Path]) -> None:
    _, db = paths

    log_usage(
        db,
        "ai_call",
        "classify",
        detail="note a.md",
        model="gpt-x",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.0125,
        now=NOW,
    )

    assert _rows(db, "SELECT * FROM usage_events") == [
        (NOW, "ai_call", "classify", "note a.md", "gpt-x", 100, 20, 0.0125)
    ]


def test_log_usage_defaults_are_null_and_appends(paths: tuple[Path, Path]) -> None:
    _, db = paths

    log_usage(db, "screen_view", "topic-map", now=NOW)
    log_usage(db, "action", "select-topic", now=NOW)

    assert _rows(db, "SELECT kind, name, detail, model, cost_usd FROM usage_events") == [
        ("screen_view", "topic-map", None, None, None),
        ("action", "select-topic", None, None, None),
    ]


def test_log_usage_uses_utc_now_by_default(paths: tuple[Path, Path]) -> None:
    _, db = paths

    log_usage(db, "action", "x")

    ((ts,),) = _rows(db, "SELECT ts FROM usage_events")
    assert abs((datetime.utcnow() - ts).total_seconds()) < 60


def test_usage_survives_a_full_scan(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    _write(vault, "a.md", _note("A"), T0)
    log_usage(db, "action", "x", now=NOW)

    refresh_notes(vault, db)
    refresh_notes_incremental(vault, db)

    assert _rows(db, "SELECT name FROM usage_events") == [("x",)]


def test_post_usage_logs_event(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    client = TestClient(create_app(_settings(vault)))

    response = client.post(
        "/usage", json={"kind": "screen_view", "name": "topic-map", "detail": "first"}
    )

    assert (response.status_code, response.content) == (204, b"")
    assert _rows(db, "SELECT kind, name, detail, model, cost_usd FROM usage_events") == [
        ("screen_view", "topic-map", "first", None, None)
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "ai_call", "name": "x"},
        {"kind": "action", "name": ""},
        {"kind": "action", "name": "x" * 201},
        {"kind": "action", "name": "x", "detail": "d" * 1001},
        {"kind": "action"},
    ],
)
def test_post_usage_rejects_bad_events(paths: tuple[Path, Path], body: dict) -> None:
    vault, db = paths
    client = TestClient(create_app(_settings(vault)))

    response = client.post("/usage", json=body)

    assert response.status_code == 422
    assert not db.exists()


def test_post_usage_accepts_limits(paths: tuple[Path, Path]) -> None:
    vault, db = paths
    client = TestClient(create_app(_settings(vault)))

    response = client.post(
        "/usage", json={"kind": "action", "name": "x" * 200, "detail": "d" * 1000}
    )

    assert response.status_code == 204
