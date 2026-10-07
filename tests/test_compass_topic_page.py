"""Tests for the topic page API (T1, T2, T5, T6) and the signal links (M6)."""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vault_compass.app import create_app
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.signals import SIGNAL_RULES
from vault_compass.topics import load_topics

TODAY = date(2026, 10, 6)

TOPICS_YAML = (
    "topics:\n"
    "  big:\n    name: Big\n    tags: [x, x2]\n"
    "  other:\n    name: Other\n    tags: [y]\n"
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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    vault = tmp_path / "vault"
    # Evergreen in both topics: it bridges big -> other.
    _note(vault, "notes/evergreen/bridge.md", created="2026-10-06", tags="[x, y]")
    # Evergreen in big only: no bridges.
    _note(vault, "notes/evergreen/solo.md", created="2026-09-01", tags="[x]")
    _note(
        vault,
        "a1.md",
        created="2026-09-07",
        tags="[x]",
        extra="source_type: video\nsource_url: https://www.a.com/p1\n",
    )
    # Same source URL as a1: a likely duplicate pair.
    _note(
        vault,
        "a2.md",
        created="2026-09-07",
        tags="[x2]",
        extra="source_type: video\nsource_url: https://www.a.com/p1/\n",
    )
    _note(
        vault,
        "w1.md",
        created="2026-06-09",
        tags="[x]",
        extra="source_type: article\nauthor: Zed\n",
    )
    _note(vault, "n1.md", created="2026-06-08", tags="[x]")
    _note(vault, "nodate.md", created=None, tags="[x]")
    _note(vault, "o1.md", created="2026-09-20", tags="[y]")
    (vault / ".kai").mkdir()
    (vault / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    monkeypatch.delenv("COMPASS_TOPICS_PATH", raising=False)
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )
    return TestClient(create_app(CompassSettings(), today=lambda: TODAY))


def test_header_equals_topic_map_for_every_topic_and_window(client: TestClient) -> None:
    for window in ("30", "90", "all"):
        topic_map = client.get(f"/topic-map?window={window}").json()
        for topic in topic_map["topics"]:
            page = client.get(f"/topics/{topic['id']}?window={window}").json()

            assert page["stats"] == topic
            assert page["window"] == window


def test_header_lists_the_tags_merged_into_the_topic(client: TestClient) -> None:
    body = client.get("/topics/big").json()

    assert body["tags"] == ["x", "x2"]
    assert body["min_notes"] == 3
    assert body["stats"]["note_count"] == 7


def test_top_sources_count_unknown_separately(client: TestClient) -> None:
    body = client.get("/topics/big").json()

    # a1 and a2 share a.com (www. is dropped); Zed is an author; 3 notes have neither.
    assert body["top_sources"] == [{"name": "a.com", "notes": 2}, {"name": "Zed", "notes": 1}]
    assert body["unknown_source_notes"] == 4


def test_source_types_share_includes_unknown(client: TestClient) -> None:
    body = client.get("/topics/big").json()

    assert body["source_types"] == [
        {"source_type": "unknown", "notes": 4, "share": 4 / 7},
        {"source_type": "video", "notes": 2, "share": 2 / 7},
        {"source_type": "article", "notes": 1, "share": 1 / 7},
    ]


def test_evergreens_show_bridges_only_when_in_two_topics(client: TestClient) -> None:
    body = client.get("/topics/big").json()

    assert body["evergreens"] == [
        {
            "path": "notes/evergreen/bridge.md",
            "title": "bridge",
            "created": "2026-10-06",
            "bridges": [{"id": "other", "name": "Other"}],
        },
        {
            "path": "notes/evergreen/solo.md",
            "title": "solo",
            "created": "2026-09-01",
            "bridges": [],
        },
    ]


def test_recent_notes_newest_first_with_duplicates_marked(client: TestClient) -> None:
    body = client.get("/topics/big").json()

    assert [(n["path"], n["created"]) for n in body["recent_notes"]] == [
        ("notes/evergreen/bridge.md", "2026-10-06"),
        ("a1.md", "2026-09-07"),
        ("a2.md", "2026-09-07"),
        ("notes/evergreen/solo.md", "2026-09-01"),
        ("w1.md", "2026-06-09"),
        ("n1.md", "2026-06-08"),
        ("nodate.md", None),
    ]
    marks = {n["path"]: n["duplicates"] for n in body["recent_notes"] if n["duplicates"]}
    assert marks == {
        "a1.md": [{"path": "a2.md", "reason": "source_url"}],
        "a2.md": [{"path": "a1.md", "reason": "source_url"}],
    }


def test_topic_with_no_notes_has_empty_sections(client: TestClient) -> None:
    body = client.get("/topics/empty").json()

    assert body["stats"]["note_count"] == 0
    assert body["top_sources"] == []
    assert body["unknown_source_notes"] == 0
    assert body["source_types"] == []
    assert body["evergreens"] == []
    assert body["recent_notes"] == []
    assert body["signal"] is None


def test_unknown_topic_is_404(client: TestClient) -> None:
    response = client.get("/topics/nope")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown topic: nope"}


def test_unmapped_route_is_not_shadowed_by_topic_id(client: TestClient) -> None:
    assert client.get("/topics/unmapped").status_code == 200


def test_signal_param_lists_the_notes_behind_it(client: TestClient) -> None:
    body = client.get("/topics/big?signal=source_concentration").json()

    assert body["signal"] == {
        "kind": "source_concentration",
        "rules": SIGNAL_RULES,
        "notes": [
            {"path": "a1.md", "title": "a1", "created": "2026-09-07"},
            {"path": "a2.md", "title": "a2", "created": "2026-09-07"},
        ],
    }


def test_signal_that_does_not_fire_lists_no_notes(client: TestClient) -> None:
    body = client.get("/topics/big?signal=new_and_growing").json()

    assert body["signal"]["notes"] == []


def test_unknown_signal_kind_is_rejected(client: TestClient) -> None:
    assert client.get("/topics/big?signal=bogus").status_code == 422


def test_topic_map_signals_link_to_the_topic_page(client: TestClient) -> None:
    signals = client.get("/topic-map").json()["signals"]

    assert signals == [
        {
            "topic_id": "big",
            "topic_name": "Big",
            "kind": "source_concentration",
            "message": "Source concentration: 2 notes from one source.",
            "note_count": 2,
        }
    ]
    page = client.get(f"/topics/{signals[0]['topic_id']}?signal={signals[0]['kind']}").json()
    assert len(page["signal"]["notes"]) == signals[0]["note_count"]
