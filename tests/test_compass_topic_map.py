"""Tests for the topic map API: summary tiles and per-topic stats (M1-M5, M7)."""

import time
from datetime import date
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from vault_compass.app import NOT_SCANNED_DETAIL, create_app
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.topic_map import (
    FAST_GROWTH_PERCENT,
    MOMENTUM_FORMULA,
    _next_step,
)
from vault_compass.topics import load_topics

TODAY = date(2026, 10, 6)

TOPICS_YAML = (
    "topics:\n"
    "  big:\n    name: Big\n    tags: [x]\n"
    "  small:\n    name: Small\n    tags: [y]\n"
    "  empty:\n    name: Empty\n    tags: [q]\n"
    "min_notes: 3\ntrend_start: 2026-04-01\n"
)


def _note(vault: Path, rel: str, *, created: str | None, tags: str, extra: str = "") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    created_line = f"created: {created}\n" if created else ""
    path.write_text(
        f"---\ntitle: {path.stem}\n{created_line}tags: {tags}\n{extra}---\nbody\n",
        encoding="utf-8",
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Nine notes. Topic big holds six of them, laid out around the window edges.

    With today = 2026-10-06 the 30 day window is (2026-09-06, 2026-10-06], the
    prior 90 days are (2026-06-08, 2026-09-06], and the 90 day window starts
    after 2026-07-08.
    """
    root = tmp_path / "vault"
    # r1 is an evergreen and links to r2.
    evergreen = root / "notes/evergreen/r1.md"
    evergreen.parent.mkdir(parents=True)
    evergreen.write_text(
        "---\ntitle: r1\ncreated: 2026-10-06\ntags: [x]\n---\nsee [[r2]]\n", encoding="utf-8"
    )
    _note(
        root, "r2.md", created="2026-09-07", tags="[x]", extra="source_url: https://www.a.com/p1\n"
    )
    _note(root, "b1.md", created="2026-09-06", tags="[x]", extra="source_url: https://a.com/p2\n")
    _note(root, "b2.md", created="2026-06-09", tags="[x]", extra="source_url: https://b.com/z\n")
    _note(root, "o1.md", created="2026-06-08", tags="[x]")
    _note(root, "nodate.md", created=None, tags="[x]", extra="author: Zed\n")
    _note(root, "u1.md", created="2026-09-20", tags="[y]")
    _note(root, "inbox/i1.md", created="2026-10-01", tags="[]")
    _note(root, "inbox-old/x.md", created=None, tags="[]")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    return root


@pytest.fixture
def client(vault: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    monkeypatch.delenv("COMPASS_TOPICS_PATH", raising=False)
    monkeypatch.delenv("OBSIDIAN_INBOX_FOLDER", raising=False)
    db = vault / ".kai" / "compass.duckdb"
    refresh_notes(vault, db, load_topics(vault / ".kai" / "topics.yaml"))
    return TestClient(create_app(CompassSettings(), today=lambda: TODAY))


def _topic(body: dict, topic_id: str) -> dict:
    return next(t for t in body["topics"] if t["id"] == topic_id)


def test_tiles_count_windows_links_evergreens_and_inbox(client: TestClient) -> None:
    body = client.get("/topic-map").json()

    assert body["tiles"] == {
        "total_notes": 9,
        # r1, r2, u1, i1 are inside (2026-09-06, 2026-10-06].
        "notes_last_30": 4,
        # b1 is created exactly on 2026-09-06: prior window, not the last one.
        "notes_prior_30": 1,
        "link_share": 1 / 9,
        "evergreen_count": 1,
        # inbox-old/ must not match the inbox folder.
        "inbox_count": 1,
    }


def test_response_top_level_defaults_to_30_day_window(client: TestClient) -> None:
    body = client.get("/topic-map").json()

    assert body["window"] == "30"
    assert body["min_notes"] == 3
    assert body["momentum_formula"] == MOMENTUM_FORMULA
    assert [t["id"] for t in body["topics"]] == ["big", "small", "empty"]


def test_topic_stats_for_30_day_window(client: TestClient) -> None:
    topic = _topic(client.get("/topic-map?window=30").json(), "big")

    assert topic == {
        "id": "big",
        "name": "Big",
        "note_count": 6,
        "window_notes": 2,
        # recent 2 per 30 days vs baseline 2 per 90 days (0.667 a month): +200%.
        "momentum": 200.0,
        "evergreens": 1,
        "linked_notes": 1,
        "notes_per_month": [
            {"month": "2026-04", "notes": 0},
            {"month": "2026-05", "notes": 0},
            {"month": "2026-06", "notes": 2},
            {"month": "2026-07", "notes": 0},
            {"month": "2026-08", "notes": 0},
            {"month": "2026-09", "notes": 2},
            {"month": "2026-10", "notes": 1},
        ],
        "top_source": {"name": "a.com", "notes": 2},
        "below_min_notes": False,
        "next_step": "Growing fast. Turn the recent notes into an evergreen.",
    }


def test_topic_stats_for_90_day_window(client: TestClient) -> None:
    topic = _topic(client.get("/topic-map?window=90").json(), "big")

    assert topic["window_notes"] == 3
    # recent 3 per 90 days is 1 a month; baseline 2 per 90 days is 0.667: +50%.
    assert topic["momentum"] == 50.0


def test_topic_stats_for_all_time_window(client: TestClient) -> None:
    body = client.get("/topic-map?window=all").json()
    topic = _topic(body, "big")

    assert body["window"] == "all"
    assert topic["window_notes"] == 6
    # recent 2 a month vs 3 notes over the 159 days since trend start.
    assert topic["momentum"] == round((2 / (3 / (159 / 30)) - 1) * 100, 1)
    assert topic["momentum"] == 253.3


def test_unknown_window_is_rejected(client: TestClient) -> None:
    assert client.get("/topic-map?window=7").status_code == 422


def test_small_and_empty_topics(client: TestClient) -> None:
    body = client.get("/topic-map").json()

    assert _topic(body, "small") == {
        "id": "small",
        "name": "Small",
        "note_count": 1,
        "window_notes": 1,
        "momentum": None,
        "evergreens": 0,
        "linked_notes": 0,
        "notes_per_month": _topic(body, "small")["notes_per_month"],
        "top_source": None,
        "below_min_notes": True,
        "next_step": "Too few notes to read a trend. Keep collecting.",
    }
    empty = _topic(body, "empty")
    assert empty["note_count"] == 0
    assert empty["momentum"] is None
    assert empty["top_source"] is None
    assert empty["notes_per_month"][0] == {"month": "2026-04", "notes": 0}


def test_numbers_match_direct_duckdb_queries(client: TestClient, vault: Path) -> None:
    body = client.get("/topic-map").json()
    con = duckdb.connect(str(vault / ".kai" / "compass.duckdb"), read_only=True)
    try:
        direct_count = con.execute(
            "SELECT COUNT(*) FROM note_topics WHERE topic = 'big'"
        ).fetchone()
        direct_evergreens = con.execute(
            "SELECT COUNT(*) FROM note_topics nt JOIN notes n ON n.path = nt.path "
            "WHERE nt.topic = 'big' AND n.is_evergreen"
        ).fetchone()
        direct_total = con.execute("SELECT COUNT(*) FROM notes").fetchone()
    finally:
        con.close()

    big = _topic(body, "big")
    assert (big["note_count"], big["evergreens"]) == (direct_count[0], direct_evergreens[0])
    assert body["tiles"]["total_notes"] == direct_total[0]


def test_top_source_tie_goes_to_the_name_that_sorts_first(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _note(
        vault, "t1.md", created="2026-09-10", tags="[tie]", extra="source_url: https://zz.com/a\n"
    )
    _note(vault, "t2.md", created="2026-09-10", tags="[tie]", extra="author: Alpha\n")
    (vault / ".kai" / "topics.yaml").write_text(
        TOPICS_YAML.replace("  empty:", "  tie:\n    name: Tie\n    tags: [tie]\n  empty:"),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )

    body = TestClient(create_app(CompassSettings(), today=lambda: TODAY)).get("/topic-map").json()

    # An author stands in when a note has no URL; "Alpha" sorts before "zz.com".
    assert _topic(body, "tie")["top_source"] == {"name": "Alpha", "notes": 1}


def test_inbox_folder_follows_setting(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("OBSIDIAN_INBOX_FOLDER", "inbox-old")
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )

    body = TestClient(create_app(CompassSettings(), today=lambda: TODAY)).get("/topic-map").json()

    assert body["tiles"]["inbox_count"] == 1


def test_empty_vault_has_zero_link_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "vault"
    (root / ".kai").mkdir(parents=True)
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(root))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    refresh_notes(
        root, root / ".kai" / "compass.duckdb", load_topics(root / ".kai" / "topics.yaml")
    )

    body = TestClient(create_app(CompassSettings(), today=lambda: TODAY)).get("/topic-map").json()

    assert body["tiles"]["link_share"] == 0.0
    assert body["tiles"]["total_notes"] == 0


def test_missing_database_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "vault"
    (root / ".kai").mkdir(parents=True)
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(root))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)

    response = TestClient(create_app(CompassSettings())).get("/topic-map")

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_SCANNED_DETAIL}


def test_invalid_topics_file_is_500(client: TestClient, vault: Path) -> None:
    (vault / ".kai" / "topics.yaml").write_text("topics: nope\n", encoding="utf-8")

    response = client.get("/topic-map")

    assert response.status_code == 500


def test_loads_in_under_one_second_for_a_realistic_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N5: about 1,100 notes. Rows are inserted directly; scanning files is not under test."""
    root = tmp_path / "vault"
    (root / ".kai").mkdir(parents=True)
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    db = root / ".kai" / "compass.duckdb"
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(root))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    refresh_notes(root, db, load_topics(root / ".kai" / "topics.yaml"))
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "INSERT INTO notes SELECT 'n' || i || '.md', 'n' || i, DATE '2026-09-15', "
            "NULL, 'https://a.com/p', NULL, '', 10, false FROM range(1100) t(i)"
        )
        con.execute(
            "INSERT INTO note_tags SELECT 'n' || i || '.md', tag "
            "FROM range(1100) t(i), (VALUES ('x'), ('y')) v(tag)"
        )
    finally:
        con.close()
    client = TestClient(create_app(CompassSettings(), today=lambda: TODAY))

    started = time.perf_counter()
    response = client.get("/topic-map")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert _topic(response.json(), "big")["note_count"] == 1100
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Suggested next step rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"below_min": True, "evergreens": 0, "momentum": 900.0, "unlinked": 5},
            "Too few notes to read a trend. Keep collecting.",
        ),
        (
            {"below_min": False, "evergreens": 0, "momentum": 900.0, "unlinked": 5},
            "Write your first evergreen note on this topic.",
        ),
        (
            {"below_min": False, "evergreens": 1, "momentum": FAST_GROWTH_PERCENT, "unlinked": 0},
            "Growing fast. Turn the recent notes into an evergreen.",
        ),
        (
            {"below_min": False, "evergreens": 1, "momentum": 49.9, "unlinked": 1},
            "Link the notes that have no links yet.",
        ),
        (
            {"below_min": False, "evergreens": 1, "momentum": None, "unlinked": 1},
            "Link the notes that have no links yet.",
        ),
        (
            {"below_min": False, "evergreens": 1, "momentum": 49.9, "unlinked": 0},
            "Revisit your evergreens and look for gaps.",
        ),
    ],
)
def test_next_step_rules(kwargs: dict, expected: str) -> None:
    assert _next_step(**kwargs) == expected
