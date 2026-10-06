"""Tests for Vault Compass topic definitions (D4, ADR 0003)."""

from datetime import date
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from vault_compass import cli as compass_cli
from vault_compass.app import NOT_SCANNED_DETAIL, create_app
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.topics import (
    SEED_TOPICS_YAML,
    Topic,
    TopicsError,
    TopicsFile,
    load_topics,
    parse_topics,
    seed_topics_file,
)

runner = CliRunner()

MINIMAL = "topics:\n  a:\n    name: A\n    tags: [x]\n"


def _write(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _query(db: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _topics(**overrides: object) -> TopicsFile:
    data: dict = {"topics": {"a": {"name": "A", "tags": ["x"]}}}
    data.update(overrides)
    return TopicsFile.model_validate(data)


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------


def test_minimal_file_gets_adr_defaults() -> None:
    parsed = parse_topics(MINIMAL)

    assert parsed.min_notes == 10
    assert parsed.trend_start == date(2026, 4, 1)
    assert parsed.ai_exclude_folders == ["notes/reflections"]
    assert parsed.topics == {"a": Topic(name="A", tags=["x"])}


def test_explicit_settings_override_defaults() -> None:
    parsed = parse_topics(
        MINIMAL + "min_notes: 0\ntrend_start: 2026-06-15\nai_exclude_folders: [a/b/, c]\n"
    )

    assert parsed.min_notes == 0
    assert parsed.trend_start == date(2026, 6, 15)
    assert parsed.ai_exclude_folders == ["a/b", "c"]


def test_tags_are_trimmed_and_lose_leading_hash() -> None:
    parsed = parse_topics("topics:\n  a:\n    name: A\n    tags: ['#x', ' y ']\n")

    assert parsed.topics["a"].tags == ["x", "y"]


def test_topic_tag_pairs_follow_file_order_and_allow_shared_tags() -> None:
    parsed = parse_topics(
        "topics:\n  b:\n    name: B\n    tags: [x, y]\n  a:\n    name: A\n    tags: [x]\n"
    )

    assert parsed.topic_tag_pairs() == [("b", "x"), ("b", "y"), ("a", "x")]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "topics file must be a mapping with a 'topics' key"),
        ("- a\n- b\n", "topics file must be a mapping with a 'topics' key"),
        ("min_notes: 5\n", "topics file is invalid: topics: Field required"),
        (
            "topics: {}\n",
            "topics file is invalid: topics: "
            "Dictionary should have at least 1 item after validation, not 0",
        ),
        (
            MINIMAL + "mid_notes: 3\n",
            "topics file is invalid: mid_notes: Extra inputs are not permitted",
        ),
        (
            "topics:\n  a:\n    name: A\n    tags: [x]\n    colour: red\n",
            "topics file is invalid: topics.a.colour: Extra inputs are not permitted",
        ),
        (
            "topics:\n  A B:\n    name: A\n    tags: [x]\n",
            "topics file is invalid: topics.A B.[key]: "
            "String should match pattern '^[a-z0-9][a-z0-9-]*$'",
        ),
        (
            "topics:\n  a:\n    name: ''\n    tags: [x]\n",
            "topics file is invalid: topics.a.name: String should have at least 1 character",
        ),
        (
            "topics:\n  a:\n    name: A\n    tags: []\n",
            "topics file is invalid: topics.a.tags: "
            "List should have at least 1 item after validation, not 0",
        ),
        (
            "topics:\n  a:\n    name: A\n    tags: [x, '#x']\n",
            "topics file is invalid: topics.a.tags: Value error, duplicate tags: x",
        ),
        (
            "topics:\n  a:\n    name: A\n    tags: [x, ' ']\n",
            "topics file is invalid: topics.a.tags: Value error, tags must not be blank",
        ),
        (
            # YAML reads a bare `no` as the boolean False, a classic tag-list trap.
            "topics:\n  a:\n    name: A\n    tags: [no]\n",
            "topics file is invalid: topics.a.tags.0: Input should be a valid string",
        ),
        (
            MINIMAL + "min_notes: -1\n",
            "topics file is invalid: min_notes: Input should be greater than or equal to 0",
        ),
        (
            MINIMAL + "trend_start: soon\n",
            "topics file is invalid: trend_start: "
            "Input should be a valid date or datetime, input is too short",
        ),
    ],
)
def test_invalid_files_give_exact_messages(text: str, message: str) -> None:
    with pytest.raises(TopicsError) as exc_info:
        parse_topics(text)

    assert str(exc_info.value) == message


def test_malformed_yaml_is_a_topics_error() -> None:
    with pytest.raises(TopicsError) as exc_info:
        parse_topics("topics: [")

    assert str(exc_info.value).startswith("topics file is not valid YAML: ")


def test_multiple_problems_are_joined_with_semicolons() -> None:
    with pytest.raises(TopicsError) as exc_info:
        parse_topics(MINIMAL + "min_notes: -1\nbogus: 1\n")

    assert str(exc_info.value) == (
        "topics file is invalid: min_notes: Input should be greater than or equal to 0; "
        "bogus: Extra inputs are not permitted"
    )


def test_topic_id_accepts_digits_and_inner_hyphens() -> None:
    parsed = parse_topics("topics:\n  3d-print-2:\n    name: P\n    tags: [x]\n")

    assert list(parsed.topics) == ["3d-print-2"]


# ---------------------------------------------------------------------------
# Loading and seeding
# ---------------------------------------------------------------------------


def test_load_topics_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "t.yaml"
    path.write_text(MINIMAL, encoding="utf-8")

    assert load_topics(path) == parse_topics(MINIMAL)


def test_load_topics_missing_file_message(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"

    with pytest.raises(TopicsError) as exc_info:
        load_topics(path)

    assert str(exc_info.value) == (
        f"cannot read topics file {path}: [Errno 2] No such file or directory: '{path}'"
    )


def test_load_topics_undecodable_file_is_a_topics_error(tmp_path: Path) -> None:
    path = tmp_path / "bin.yaml"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(TopicsError) as exc_info:
        load_topics(path)

    assert str(exc_info.value).startswith(f"cannot read topics file {path}: 'utf-8' codec")


def test_seed_has_nine_topics_with_adr_settings() -> None:
    seed = parse_topics(SEED_TOPICS_YAML)

    assert len(seed.topics) == 9
    assert seed.topics["ai-assisted-development"] == Topic(
        name="AI-assisted development",
        tags=[
            "agentic-engineering",
            "coding-agents",
            "agentic-ai",
            "ai-assisted-development",
            "claude-code",
            "agent-harnesses",
            "prompt-engineering",
            "developer-tools",
        ],
    )
    assert seed.min_notes == 10
    assert seed.trend_start == date(2026, 4, 1)
    assert seed.ai_exclude_folders == ["notes/reflections"]


def test_seed_topics_file_creates_parent_and_writes_seed(tmp_path: Path) -> None:
    path = tmp_path / ".kai" / "topics.yaml"

    assert seed_topics_file(path) is True
    assert path.read_text(encoding="utf-8") == SEED_TOPICS_YAML


def test_seed_topics_file_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text("mine", encoding="utf-8")

    assert seed_topics_file(path) is False
    assert path.read_text(encoding="utf-8") == "mine"


# ---------------------------------------------------------------------------
# note_topics view and unmapped tags
# ---------------------------------------------------------------------------


def _tagged_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    _write(vault, "n1.md", "---\ntags: [x, y, z]\n---\nb")
    _write(vault, "n2.md", "---\ntags: [y, z]\n---\nb")
    _write(vault, "n3.md", "---\ntags: [z]\n---\nb")
    _write(vault, "n4.md", "no tags")
    return vault


def test_note_topics_maps_each_tag_to_every_topic_that_lists_it(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    topics = TopicsFile.model_validate(
        {
            "topics": {
                "one": {"name": "One", "tags": ["x", "y"]},
                "two": {"name": "Two", "tags": ["y"]},
            }
        }
    )

    refresh_notes(_tagged_vault(tmp_path), db, topics)

    assert _query(db, "SELECT path, topic FROM note_topics ORDER BY path, topic") == [
        ("n1.md", "one"),
        ("n1.md", "two"),
        ("n2.md", "one"),
        ("n2.md", "two"),
    ]
    assert _query(db, "SELECT topic, tag FROM topic_tags ORDER BY topic, tag") == [
        ("one", "x"),
        ("one", "y"),
        ("two", "y"),
    ]


def test_note_with_two_tags_of_one_topic_appears_once(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    topics = TopicsFile.model_validate({"topics": {"one": {"name": "One", "tags": ["x", "y"]}}})

    refresh_notes(_tagged_vault(tmp_path), db, topics)

    assert _query(db, "SELECT path FROM note_topics WHERE topic = 'one' ORDER BY path") == [
        ("n1.md",),
        ("n2.md",),
    ]


def test_unmapped_tags_are_counted_and_sorted_by_use_then_name(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [b, a, mapped]\n---\nb")
    _write(vault, "b.md", "---\ntags: [b, a, c]\n---\nb")
    _write(vault, "c.md", "---\ntags: [b]\n---\nb")
    topics = TopicsFile.model_validate({"topics": {"t": {"name": "T", "tags": ["mapped"]}}})

    report = refresh_notes(vault, tmp_path / "c.duckdb", topics)

    assert report.unmapped_tags == [("b", 3), ("a", 2), ("c", 1)]


def test_every_tag_is_mapped_or_unmapped(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    topics = TopicsFile.model_validate({"topics": {"one": {"name": "One", "tags": ["x"]}}})

    report = refresh_notes(_tagged_vault(tmp_path), db, topics)

    mapped = {tag for (tag,) in _query(db, "SELECT DISTINCT tag FROM note_tags") if tag == "x"}
    assert mapped == {"x"}
    assert report.unmapped_tags == [("z", 3), ("y", 2)]


def test_topic_tag_matching_is_exact(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [AI]\n---\nb")
    topics = TopicsFile.model_validate({"topics": {"t": {"name": "T", "tags": ["ai"]}}})

    report = refresh_notes(vault, tmp_path / "c.duckdb", topics)

    assert report.unmapped_tags == [("AI", 1)]


def test_refresh_without_topics_leaves_no_topic_objects(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"

    report = refresh_notes(_tagged_vault(tmp_path), db)

    assert report.unmapped_tags == []
    assert (
        _query(
            db,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('topic_tags', 'note_topics')",
        )
        == []
    )


def test_second_refresh_replaces_topic_data(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    vault = _tagged_vault(tmp_path)
    refresh_notes(vault, db, _topics())

    refresh_notes(
        vault, db, TopicsFile.model_validate({"topics": {"b": {"name": "B", "tags": ["y"]}}})
    )

    assert _query(db, "SELECT topic, tag FROM topic_tags") == [("b", "y")]
    assert _query(db, "SELECT path, topic FROM note_topics ORDER BY path") == [
        ("n1.md", "b"),
        ("n2.md", "b"),
    ]


def test_refresh_without_topics_drops_stale_topic_data(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    vault = _tagged_vault(tmp_path)
    refresh_notes(vault, db, _topics())

    refresh_notes(vault, db)

    assert (
        _query(
            db,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('topic_tags', 'note_topics')",
        )
        == []
    )


def test_empty_vault_with_topics(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    db = tmp_path / "c.duckdb"

    report = refresh_notes(vault, db, _topics())

    assert report.unmapped_tags == []
    assert _query(db, "SELECT count(*) FROM note_topics") == [(0,)]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_compass_topics_path_defaults_under_vault_kai_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_TOPICS_PATH", raising=False)

    assert CompassSettings().compass_topics_path == vault.resolve() / ".kai" / "topics.yaml"


def test_compass_topics_path_override_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("COMPASS_TOPICS_PATH", str(tmp_path / "my.yaml"))

    assert CompassSettings().compass_topics_path == tmp_path / "my.yaml"


# ---------------------------------------------------------------------------
# compass scan
# ---------------------------------------------------------------------------


def _scan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vault: Path) -> None:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    monkeypatch.delenv("COMPASS_TOPICS_PATH", raising=False)
    monkeypatch.setattr("obsidian_ai_tools.config.find_env_file", lambda: None)


def test_scan_seeds_missing_file_and_reports_unmapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ncreated: 2026-01-01\ntags: [ai, odd]\n---\nx")
    _scan_env(tmp_path, monkeypatch, vault)

    result = runner.invoke(compass_cli.app, ["scan"])

    topics_file = vault.resolve() / ".kai" / "topics.yaml"
    assert result.exit_code == 0
    assert result.output == (
        f"Created {topics_file} with the starter topics\n"
        "Notes: 1\nUnparsed dates: 0\nUndated notes: 0\n"
        "Unresolved links: 0\nUnmapped tags: 1\nLikely duplicates: 0\n"
    )
    assert topics_file.read_text(encoding="utf-8") == SEED_TOPICS_YAML


def test_scan_keeps_existing_file_and_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ncreated: 2026-01-01\ntags: [x]\n---\nx")
    _write(vault, ".kai/topics.yaml", MINIMAL)
    _scan_env(tmp_path, monkeypatch, vault)

    result = runner.invoke(compass_cli.app, ["scan"])

    assert result.exit_code == 0
    assert result.output == (
        "Notes: 1\nUnparsed dates: 0\nUndated notes: 0\n"
        "Unresolved links: 0\nUnmapped tags: 0\nLikely duplicates: 0\n"
    )
    assert (vault / ".kai" / "topics.yaml").read_text(encoding="utf-8") == MINIMAL
    assert _query(vault / ".kai" / "compass.duckdb", "SELECT path, topic FROM note_topics") == [
        ("a.md", "a")
    ]


def test_scan_invalid_topics_file_exits_1_without_touching_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "x")
    _write(vault, ".kai/topics.yaml", "min_notes: 5\n")
    _scan_env(tmp_path, monkeypatch, vault)

    result = runner.invoke(compass_cli.app, ["scan"])

    assert result.exit_code == 1
    assert result.output == "❌ topics file is invalid: topics: Field required\n"
    assert not (vault / ".kai" / "compass.duckdb").exists()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vault: Path) -> TestClient:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    monkeypatch.delenv("COMPASS_TOPICS_PATH", raising=False)
    return TestClient(create_app(CompassSettings()))


def test_unmapped_endpoint_lists_tags_with_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [x, odd, rare]\n---\nb")
    _write(vault, "b.md", "---\ntags: [odd]\n---\nb")
    _write(vault, ".kai/topics.yaml", MINIMAL)
    client = _client(tmp_path, monkeypatch, vault)
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )

    response = client.get("/topics/unmapped")

    assert response.status_code == 200
    assert response.json() == [{"tag": "odd", "notes": 2}, {"tag": "rare", "notes": 1}]


def test_unmapped_endpoint_is_empty_when_all_tags_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [x]\n---\nb")
    _write(vault, ".kai/topics.yaml", MINIMAL)
    client = _client(tmp_path, monkeypatch, vault)
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )

    response = client.get("/topics/unmapped")

    assert response.status_code == 200
    assert response.json() == []


def test_unmapped_endpoint_before_first_scan_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    client = _client(tmp_path, monkeypatch, vault)

    response = client.get("/topics/unmapped")

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_SCANNED_DETAIL}


def test_unmapped_endpoint_with_pre_topics_database_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [x]\n---\nb")
    client = _client(tmp_path, monkeypatch, vault)
    refresh_notes(vault, vault / ".kai" / "compass.duckdb")  # scanned without topics

    response = client.get("/topics/unmapped")

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_SCANNED_DETAIL}


def test_topics_endpoint_returns_definitions_counts_and_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [x]\n---\nb")
    _write(vault, "b.md", "---\ntags: [x, y]\n---\nb")
    _write(
        vault,
        ".kai/topics.yaml",
        "topics:\n  big:\n    name: Big\n    tags: [x]\n"
        "  small:\n    name: Small\n    tags: [y]\n  empty:\n    name: Empty\n    tags: [q]\n"
        "min_notes: 2\ntrend_start: 2026-05-01\nai_exclude_folders: [priv]\n",
    )
    client = _client(tmp_path, monkeypatch, vault)
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )

    response = client.get("/topics")

    assert response.status_code == 200
    assert response.json() == {
        "min_notes": 2,
        "trend_start": "2026-05-01",
        "ai_exclude_folders": ["priv"],
        "topics": [
            {"id": "big", "name": "Big", "tags": ["x"], "note_count": 2, "below_min_notes": False},
            {
                "id": "small",
                "name": "Small",
                "tags": ["y"],
                "note_count": 1,
                "below_min_notes": True,
            },
            {
                "id": "empty",
                "name": "Empty",
                "tags": ["q"],
                "note_count": 0,
                "below_min_notes": True,
            },
        ],
    }


def test_topics_endpoint_invalid_file_is_500_with_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, ".kai/topics.yaml", "min_notes: 5\n")
    client = _client(tmp_path, monkeypatch, vault)

    response = client.get("/topics")

    assert response.status_code == 500
    assert response.json() == {"detail": "topics file is invalid: topics: Field required"}


def test_endpoints_load_settings_from_environment_when_none_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [x]\n---\nb")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    monkeypatch.setattr("obsidian_ai_tools.config.find_env_file", lambda: None)
    refresh_notes(vault, vault / ".kai" / "compass.duckdb", _topics())

    response = TestClient(create_app()).get("/topics/unmapped")

    assert response.json() == []
