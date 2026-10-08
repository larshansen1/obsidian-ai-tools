"""New notes through the write layer, and Draft new evergreen (T8): #138."""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from vault_compass.app import create_app
from vault_compass.claims import EligibleNote, claim_id, save_claims
from vault_compass.config import CompassSettings
from vault_compass.db import writable
from vault_compass.evergreen_draft import (
    SOURCES_HEADING,
    draft_body,
    evergreen_text,
    plan_evergreen,
    slugify,
)
from vault_compass.notes import refresh_notes
from vault_compass.topics import load_topics
from vault_compass.vault_writes import (
    DiffLine,
    FileEdit,
    FilePreview,
    LoggedWrite,
    PendingWrite,
    WriteError,
    WriteOutcome,
    apply_write,
    atomic_write,
    cancel_write,
    plan_write,
    read_for_edit,
    recent_writes,
    undo_write,
)

T0 = datetime(2026, 10, 8, 12, 0, 0)
TODAY = date(2026, 10, 8)

TOPICS_YAML = (
    "topics:\n"
    "  sleep:\n    name: Sleep\n    tags: [sleep, rest]\n"
    "  other:\n    name: Other\n    tags: [z]\n"
    "min_notes: 1\ntrend_start: 2026-04-01\n"
    "ai_exclude_folders: [notes/reflections]\n"
)

NEW_TEXT = (
    "---\ntitle: Sleep is a skill\ntype: evergreen\ncreated: 2026-10-08\n"
    "tags:\n- evergreen\n- sleep\n---\n\n# Sleep is a skill\n\nMy words.\n"
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "compass.duckdb"


def _new(path: Path, text: str = "new\n") -> FileEdit:
    return FileEdit(path, path.name, None, text.encode(), None)


# ---------------------------------------------------------------------------
# Write layer: creating a note
# ---------------------------------------------------------------------------


def test_planning_a_new_note_previews_every_line_and_writes_nothing(
    db: Path, tmp_path: Path
) -> None:
    path = tmp_path / "n.md"

    pending = plan_write(db, "evergreen", "New", [_new(path, "a\nb\n")], now=T0)

    assert pending == PendingWrite(
        id=pending.id,
        kind="evergreen",
        summary="New",
        files=[
            FilePreview(file="n.md", lines=[DiffLine(op="+", text="a"), DiffLine(op="+", text="b")])
        ],
    )
    assert not path.exists()


def test_applying_a_new_note_creates_it_and_undo_removes_it(db: Path, tmp_path: Path) -> None:
    path = tmp_path / "n.md"
    pending = plan_write(db, "evergreen", "New", [_new(path)], now=T0)

    written = apply_write(db, pending.id, now=T0)

    assert written == WriteOutcome(id=written.id, status="written", message="New")
    assert path.read_bytes() == b"new\n"
    assert not (tmp_path / ".n.md.compass-tmp").exists()

    undone = undo_write(db, written.id, now=T0)

    assert undone == WriteOutcome(id=undone.id, status="undone", message="Undone: New")
    assert not path.exists()
    assert [(w.summary, w.files, w.undone) for w in recent_writes(db)] == [
        ("Undo: New", ["n.md"], False),
        ("New", ["n.md"], True),
    ]


def test_a_new_note_is_refused_when_the_name_was_taken_after_the_preview(
    db: Path, tmp_path: Path
) -> None:
    path = tmp_path / "n.md"
    pending = plan_write(db, "evergreen", "New", [_new(path)], now=T0)
    path.write_bytes(b"made in Obsidian\n")

    outcome = apply_write(db, pending.id, now=T0)

    assert outcome == WriteOutcome(
        id=pending.id,
        status="refused",
        message="n.md changed since it was read. Nothing was written. "
        "Preview the change again, then retry.",
    )
    assert path.read_bytes() == b"made in Obsidian\n"
    assert recent_writes(db) == []


def test_undoing_a_new_note_is_refused_after_it_was_edited(db: Path, tmp_path: Path) -> None:
    path = tmp_path / "n.md"
    written = apply_write(db, plan_write(db, "evergreen", "New", [_new(path)]).id)
    path.write_bytes(b"new\nmore\n")

    outcome = undo_write(db, written.id, now=T0)

    assert outcome == WriteOutcome(
        id=written.id,
        status="refused",
        message="n.md changed after this write, so it was not undone. Undo the later writes first.",
    )
    assert path.read_bytes() == b"new\nmore\n"


def test_undoing_a_new_note_that_was_deleted_is_refused(db: Path, tmp_path: Path) -> None:
    path = tmp_path / "n.md"
    written = apply_write(db, plan_write(db, "evergreen", "New", [_new(path)]).id)
    path.unlink()

    assert undo_write(db, written.id).status == "refused"
    assert not path.exists()


def test_create_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "n.md"
    path.write_bytes(b"there\n")

    with pytest.raises(FileExistsError):
        atomic_write(path, b"new\n", create=True)

    assert path.read_bytes() == b"there\n"
    assert not (tmp_path / ".n.md.compass-tmp").exists()


def test_a_failed_write_removes_the_new_note_it_already_made(db: Path, tmp_path: Path) -> None:
    new = tmp_path / "n.md"
    old = tmp_path / "old.md"
    old.write_bytes(b"old\n")
    before, mtime = read_for_edit(old)
    edits = [_new(new), FileEdit(old, "old.md", before, b"changed\n", mtime)]
    pending = plan_write(db, "test", "s", edits)
    real = atomic_write

    def flaky(path: Path, data: bytes, *, create: bool = False) -> None:
        if path == old:
            raise OSError("disk full")
        real(path, data, create=create)

    with patch("vault_compass.vault_writes.atomic_write", side_effect=flaky):
        with pytest.raises(WriteError) as exc_info:
            apply_write(db, pending.id)

    assert str(exc_info.value) == "Could not write old.md: disk full. Nothing was changed."
    assert not new.exists()
    assert old.read_bytes() == b"old\n"


# Tables as #137 made them, before a write could have no "before" file.
_OLD_TABLES = (
    "CREATE TABLE pending_writes (id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL, "
    "summary VARCHAR NOT NULL, created TIMESTAMP NOT NULL)",
    "CREATE TABLE pending_files (write_id VARCHAR NOT NULL, seq INTEGER NOT NULL, "
    "path VARCHAR NOT NULL, label VARCHAR NOT NULL, mtime_ns BIGINT NOT NULL, "
    "before BLOB NOT NULL, after BLOB NOT NULL)",
    "CREATE TABLE write_log (id VARCHAR PRIMARY KEY, seq BIGINT NOT NULL, kind VARCHAR NOT NULL, "
    "summary VARCHAR NOT NULL, written_at TIMESTAMP NOT NULL, undone_at TIMESTAMP)",
    "CREATE TABLE write_files (write_id VARCHAR NOT NULL, seq INTEGER NOT NULL, "
    "path VARCHAR NOT NULL, label VARCHAR NOT NULL, before BLOB NOT NULL, after BLOB NOT NULL)",
)


def test_tables_from_before_new_notes_are_upgraded(db: Path, tmp_path: Path) -> None:
    with writable(db) as con:
        for ddl in _OLD_TABLES:
            con.execute(ddl)
        con.execute("INSERT INTO write_log VALUES ('old', 1, 'link_notes', 'Old', ?, NULL)", [T0])
        con.execute("INSERT INTO write_files VALUES ('old', 0, 'x', 'x.md', 'a', 'b')")
    path = tmp_path / "n.md"

    written = apply_write(db, plan_write(db, "evergreen", "New", [_new(path)], now=T0).id, now=T0)
    undo_write(db, written.id, now=T0)

    assert not path.exists()
    assert [(w.summary, w.files) for w in recent_writes(db)] == [
        ("Undo: New", ["n.md"]),
        ("New", ["n.md"]),
        ("Old", ["x.md"]),
    ]


def test_cancelling_a_new_note_leaves_no_file_and_nothing_pending(db: Path, tmp_path: Path) -> None:
    path = tmp_path / "n.md"
    pending = plan_write(db, "evergreen", "New", [_new(path)])

    cancel_write(db, pending.id)

    assert not path.exists()
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM pending_files").fetchone() == (0,)
        assert con.execute("SELECT COUNT(*) FROM pending_writes").fetchone() == (0,)


# ---------------------------------------------------------------------------
# Draft new evergreen (T8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Sleep is a skill", "sleep-is-a-skill"),
        ("  Coherence: the invariant!  ", "coherence-the-invariant"),
        ("Æble på brød, øl & Straße", "aeble-pa-brod-ol-strasse"),
        ("Œuvre đ ł", "oeuvre-d-l"),
        ("--x--", "x"),
        ("?!", ""),
        ("a" * 79 + " b", "a" * 79),
        ("a" * 81, "a" * 80),
    ],
)
def test_slugify(title: str, slug: str) -> None:
    assert slugify(title) == slug


def test_evergreen_text_matches_the_vaults_evergreens() -> None:
    assert evergreen_text("Sleep is a skill", "sleep", "\nMy words.\r\n\n", TODAY) == NEW_TEXT


def test_evergreen_text_quotes_a_title_yaml_would_misread() -> None:
    text = evergreen_text("Sleep: a skill", "sleep", "", TODAY)

    assert text == (
        "---\ntitle: 'Sleep: a skill'\ntype: evergreen\ncreated: 2026-10-08\n"
        "tags:\n- evergreen\n- sleep\n---\n\n# Sleep: a skill\n"
    )


def _note(root: Path, rel: str, body: str, tags: str = "[sleep]") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {path.stem}\ntags: {tags}\n---\n{body}\n", encoding="utf-8")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _note(root, "notes/evergreen/Rest matters.md", "Evergreen.")
    _note(root, "notes/a.md", "Source A.")
    _note(root, "notes/deep/b.md", "Source B.")
    _note(root, "other/b.md", "Another b.")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    db = root / ".kai" / "compass.duckdb"
    refresh_notes(root, db, load_topics(root / ".kai/topics.yaml"))
    with writable(db) as con:
        save_claims(con, EligibleNote("notes/a.md", "a", 1), [("Naps help.", "note")])
        save_claims(con, EligibleNote("notes/deep/b.md", "b", 1), [("Light\n  wakes you.", "note")])
    return root


CLAIM_A = claim_id("notes/a.md", "Naps help.")
CLAIM_B = claim_id("notes/deep/b.md", "Light\n  wakes you.")


def test_draft_body_lists_the_claims_with_their_notes(vault: Path) -> None:
    body = draft_body(vault / ".kai/compass.duckdb", [CLAIM_B, CLAIM_A, CLAIM_B])

    assert body == (f"{SOURCES_HEADING}\n\n- Light wakes you. ([[deep/b]])\n- Naps help. ([[a]])\n")


@pytest.mark.parametrize(
    ("ids", "message"),
    [
        ([], "Pick at least one claim."),
        ([f"c{i}" for i in range(51)], "Pick at most 50 claims."),
        ([CLAIM_A, "nope"], "Unknown claim: nope. Read the notes again."),
    ],
)
def test_draft_body_refusals(vault: Path, ids: list[str], message: str) -> None:
    with pytest.raises(WriteError) as exc_info:
        draft_body(vault / ".kai/compass.duckdb", ids)

    assert str(exc_info.value) == message


def test_draft_body_takes_exactly_fifty_claims(vault: Path) -> None:
    with pytest.raises(WriteError) as exc_info:
        draft_body(vault / ".kai/compass.duckdb", [CLAIM_A] + [f"c{i}" for i in range(49)])

    assert str(exc_info.value) == "Unknown claim: c0. Read the notes again."


def test_draft_body_needs_a_scan(tmp_path: Path) -> None:
    with pytest.raises(WriteError) as exc_info:
        draft_body(tmp_path / "none.duckdb", [CLAIM_A])

    assert str(exc_info.value) == "No topic data yet. Run `compass scan` first."


def test_draft_body_before_any_claims_were_read(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _note(root, "notes/a.md", "A.")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    refresh_notes(root, root / ".kai/compass.duckdb", load_topics(root / ".kai/topics.yaml"))

    with pytest.raises(WriteError) as exc_info:
        draft_body(root / ".kai/compass.duckdb", [CLAIM_A])

    assert str(exc_info.value) == f"Unknown claim: {CLAIM_A}. Read the notes again."


def test_plan_evergreen_makes_a_new_note_with_the_topics_first_tag(vault: Path) -> None:
    definitions = load_topics(vault / ".kai/topics.yaml")

    edit, summary = plan_evergreen(
        vault, definitions, "sleep", "  Sleep   is a skill ", "My words.", TODAY
    )

    assert summary == "New evergreen: Sleep is a skill"
    assert edit == FileEdit(
        vault.resolve() / "notes/evergreen/sleep-is-a-skill.md",
        "notes/evergreen/sleep-is-a-skill.md",
        None,
        NEW_TEXT.encode(),
        None,
    )


@pytest.mark.parametrize(
    ("topic", "title", "message"),
    [
        ("nope", "T", "Unknown topic: nope"),
        ("sleep", " \n ", "Give the evergreen a title."),
        ("sleep", "x" * 201, "Keep the title under 200 characters."),
        ("sleep", "?!", "The title needs at least one letter or number."),
        (
            "sleep",
            "Rest matters",
            "A note named notes/evergreen/rest-matters.md already exists. Pick another title.",
        ),
    ],
)
def test_plan_evergreen_refusals(vault: Path, topic: str, title: str, message: str) -> None:
    (vault / "notes/evergreen/rest-matters.md").write_text("x", encoding="utf-8")
    definitions = load_topics(vault / ".kai/topics.yaml")

    with pytest.raises(WriteError) as exc_info:
        plan_evergreen(vault, definitions, topic, title, "", TODAY)

    assert str(exc_info.value) == message


def test_plan_evergreen_takes_a_two_hundred_character_title(vault: Path) -> None:
    definitions = load_topics(vault / ".kai/topics.yaml")

    edit, _ = plan_evergreen(vault, definitions, "sleep", "x" * 200, "", TODAY)

    assert edit.label == f"notes/evergreen/{'x' * 80}.md"


def test_plan_evergreen_needs_the_evergreen_folder(tmp_path: Path) -> None:
    (tmp_path / ".kai").mkdir()
    (tmp_path / ".kai/topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    definitions = load_topics(tmp_path / ".kai/topics.yaml")

    with pytest.raises(WriteError) as exc_info:
        plan_evergreen(tmp_path, definitions, "sleep", "T", "", TODAY)

    assert str(exc_info.value) == "The folder notes/evergreen is missing from the vault."


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(vault: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key in ("COMPASS_DB_PATH", "COMPASS_TOPICS_PATH", "COMPASS_KAI_URL"):
        monkeypatch.delenv(key, raising=False)
    settings = CompassSettings(obsidian_vault_path=vault, openrouter_api_key=None)
    return TestClient(create_app(settings, today=lambda: TODAY))


def _evergreens(api: TestClient) -> list[str]:
    return [e["path"] for e in api.get("/topics/sleep").json()["evergreens"]]


def test_draft_flow_saves_only_after_approval(api: TestClient, vault: Path) -> None:
    draft = api.post("/topics/sleep/evergreen-draft", json={"claim_ids": [CLAIM_A]})

    assert draft.status_code == 200
    assert draft.json() == {"body": f"{SOURCES_HEADING}\n\n- Naps help. ([[a]])\n"}

    pending = api.post(
        "/writes/evergreen",
        json={"topic": "sleep", "title": "Sleep is a skill", "body": "My words."},
    )

    assert pending.status_code == 200
    assert pending.json()["summary"] == "New evergreen: Sleep is a skill"
    assert pending.json()["files"][0]["file"] == "notes/evergreen/sleep-is-a-skill.md"
    assert not (vault / "notes/evergreen/sleep-is-a-skill.md").exists()
    assert _evergreens(api) == ["notes/evergreen/Rest matters.md"]

    applied = api.post(f"/writes/{pending.json()['id']}/apply")

    assert applied.json()["status"] == "written"
    assert (vault / "notes/evergreen/sleep-is-a-skill.md").read_text(encoding="utf-8") == NEW_TEXT
    assert sorted(_evergreens(api)) == [
        "notes/evergreen/Rest matters.md",
        "notes/evergreen/sleep-is-a-skill.md",
    ]

    undone = api.post(f"/writes/{applied.json()['id']}/undo")

    assert undone.json()["status"] == "undone"
    assert not (vault / "notes/evergreen/sleep-is-a-skill.md").exists()
    assert _evergreens(api) == ["notes/evergreen/Rest matters.md"]


def test_declining_the_draft_leaves_the_vault_unchanged(api: TestClient, vault: Path) -> None:
    files = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*.md"))
    pending = api.post(
        "/writes/evergreen",
        json={"topic": "sleep", "title": "Sleep is a skill", "body": "My words."},
    )

    assert api.delete(f"/writes/{pending.json()['id']}").status_code == 204

    assert sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*.md")) == files
    assert api.post(f"/writes/{pending.json()['id']}/apply").status_code == 409
    assert api.get("/writes").json() == []


@pytest.mark.parametrize(
    ("url", "body", "status", "detail"),
    [
        ("/topics/nope/evergreen-draft", {"claim_ids": [CLAIM_A]}, 404, "Unknown topic: nope"),
        (
            "/topics/sleep/evergreen-draft",
            {"claim_ids": ["nope"]},
            400,
            "Unknown claim: nope. Read the notes again.",
        ),
        ("/writes/evergreen", {"topic": "nope", "title": "T"}, 404, "Unknown topic: nope"),
        ("/writes/evergreen", {"topic": "sleep", "title": " "}, 400, "Give the evergreen a title."),
    ],
)
def test_draft_errors(
    api: TestClient, url: str, body: dict[str, object], status: int, detail: str
) -> None:
    response = api.post(url, json=body)

    assert response.status_code == status
    assert response.json() == {"detail": detail}


def test_logged_new_note_lists_its_file(db: Path, tmp_path: Path) -> None:
    written = apply_write(db, plan_write(db, "evergreen", "New", [_new(tmp_path / "n.md")]).id, T0)

    assert recent_writes(db) == [
        LoggedWrite(
            id=written.id,
            kind="evergreen",
            summary="New",
            written_at=T0.replace(tzinfo=UTC),
            files=["n.md"],
            undone=False,
        )
    ]
