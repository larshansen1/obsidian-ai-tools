"""Safe write-back: the write layer, linking and topic editing (W1, W2, W4, W5, T7): #137."""

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vault_compass.app import create_app
from vault_compass.config import CompassSettings
from vault_compass.db import writable
from vault_compass.note_links import add_related_links, link_text, plan_links, vault_note
from vault_compass.notes import refresh_notes
from vault_compass.topic_edit import (
    TOPICS_LABEL,
    dump_topics,
    flow_list,
    new_tags,
    plan_topic_tags,
    replace_tags,
)
from vault_compass.topics import TopicsError, load_topics, parse_topics
from vault_compass.vault_writes import (
    LOG_KEEP,
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
    diff_lines,
    plan_write,
    read_for_edit,
    recent_writes,
    undo_write,
)

T0 = datetime(2026, 10, 8, 12, 0, 0)

TOPICS_YAML = (
    "topics:\n"
    "  big:\n    name: Big Topic\n    tags: [x, y]\n"
    "  other:\n    name: Other\n    tags: [z]\n"
    "min_notes: 1\ntrend_start: 2026-04-01\n"
    "ai_exclude_folders: [notes/reflections]\n"
)


def _write(vault: Path, rel: str, body: str, tags: str = "[x]") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {path.stem}\ntags: {tags}\n---\n{body}\n", encoding="utf-8")


def _edit(path: Path, after: str, label: str | None = None) -> FileEdit:
    before, mtime = read_for_edit(path)
    return FileEdit(path, label or path.name, before, after.encode(), mtime)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "compass.duckdb"


@pytest.fixture
def note(tmp_path: Path) -> Path:
    path = tmp_path / "a.md"
    path.write_bytes(b"one\ntwo\n")
    return path


# ---------------------------------------------------------------------------
# Write layer
# ---------------------------------------------------------------------------


def test_preview_writes_nothing_and_shows_the_exact_lines(db: Path, note: Path) -> None:
    pending = plan_write(db, "test", "Add three", [_edit(note, "one\ntwo\nthree\n", "a.md")])

    assert note.read_bytes() == b"one\ntwo\n"
    assert pending == PendingWrite(
        id=pending.id,
        kind="test",
        summary="Add three",
        files=[
            FilePreview(
                file="a.md",
                lines=[DiffLine(op=" ", text="two"), DiffLine(op="+", text="three")],
            )
        ],
    )
    assert recent_writes(db) == []


def test_diff_marks_removed_lines_and_gaps_between_changes() -> None:
    before = b"a\nb\nc\nd\ne\nf\n"
    after = b"A\nb\nc\nd\ne\nF\n"

    assert diff_lines(before, after) == [
        DiffLine(op="-", text="a"),
        DiffLine(op="+", text="A"),
        DiffLine(op=" ", text="b"),
        DiffLine(op="@", text=""),
        DiffLine(op=" ", text="e"),
        DiffLine(op="-", text="f"),
        DiffLine(op="+", text="F"),
    ]


def test_apply_writes_the_new_bytes_and_logs_them(db: Path, note: Path) -> None:
    pending = plan_write(db, "test", "Add three", [_edit(note, "one\ntwo\nthree\n", "a.md")])

    outcome = apply_write(db, pending.id, now=T0)

    assert outcome == WriteOutcome(id=outcome.id, status="written", message="Add three")
    assert note.read_bytes() == b"one\ntwo\nthree\n"
    assert recent_writes(db) == [
        LoggedWrite(
            id=outcome.id,
            kind="test",
            summary="Add three",
            written_at=T0.replace(tzinfo=UTC),
            files=["a.md"],
            undone=False,
        )
    ]
    assert list(note.parent.glob(".*compass-tmp")) == []


def test_apply_twice_is_refused(db: Path, note: Path) -> None:
    pending = plan_write(db, "test", "s", [_edit(note, "x\n")])
    apply_write(db, pending.id)

    with pytest.raises(WriteError) as exc_info:
        apply_write(db, pending.id)

    assert str(exc_info.value) == (
        "This change is no longer waiting for approval. Preview it again."
    )


def test_cancelled_change_cannot_be_applied(db: Path, note: Path) -> None:
    pending = plan_write(db, "test", "s", [_edit(note, "x\n")])

    cancel_write(db, pending.id)

    with pytest.raises(WriteError):
        apply_write(db, pending.id)
    assert note.read_bytes() == b"one\ntwo\n"


def test_apply_is_refused_when_the_mtime_changed(db: Path, note: Path) -> None:
    edit = _edit(note, "x\n", "a.md")
    pending = plan_write(db, "test", "s", [edit])
    # Same bytes, mtime one nanosecond later: still refused (W4).
    os.utime(note, ns=(edit.mtime_ns, edit.mtime_ns + 1))

    outcome = apply_write(db, pending.id)

    assert outcome == WriteOutcome(
        id=pending.id,
        status="refused",
        message="a.md changed since it was read. "
        "Nothing was written. Preview the change again, then retry.",
    )
    assert note.read_bytes() == b"one\ntwo\n"
    assert recent_writes(db) == []


def test_apply_is_refused_when_the_bytes_changed_but_not_the_mtime(db: Path, note: Path) -> None:
    edit = _edit(note, "x\n", "a.md")
    pending = plan_write(db, "test", "s", [edit])
    note.write_bytes(b"one\nTWO\n")
    os.utime(note, ns=(edit.mtime_ns, edit.mtime_ns))

    assert apply_write(db, pending.id).status == "refused"
    assert note.read_bytes() == b"one\nTWO\n"


def test_apply_is_refused_when_the_file_is_gone(db: Path, note: Path) -> None:
    pending = plan_write(db, "test", "s", [_edit(note, "x\n", "a.md")])
    note.unlink()

    assert apply_write(db, pending.id).status == "refused"
    assert not note.exists()


def test_refusal_on_one_file_writes_no_file(db: Path, tmp_path: Path, note: Path) -> None:
    other = tmp_path / "b.md"
    other.write_bytes(b"b\n")
    pending = plan_write(db, "test", "s", [_edit(note, "x\n", "a.md"), _edit(other, "y\n", "b.md")])
    other.write_bytes(b"changed\n")

    outcome = apply_write(db, pending.id)

    assert outcome.message.startswith("b.md changed since it was read.")
    assert note.read_bytes() == b"one\ntwo\n"


def test_failed_write_puts_back_the_files_already_written(
    db: Path, tmp_path: Path, note: Path
) -> None:
    other = tmp_path / "b.md"
    other.write_bytes(b"b\n")
    pending = plan_write(db, "test", "s", [_edit(note, "x\n", "a.md"), _edit(other, "y\n", "b.md")])
    real = atomic_write

    def flaky(path: Path, data: bytes, *, create: bool = False) -> None:
        if path == other:
            raise OSError("disk full")
        real(path, data, create=create)

    with patch("vault_compass.vault_writes.atomic_write", side_effect=flaky):
        with pytest.raises(WriteError) as exc_info:
            apply_write(db, pending.id)

    assert str(exc_info.value) == "Could not write b.md: disk full. Nothing was changed."
    assert note.read_bytes() == b"one\ntwo\n"
    assert other.read_bytes() == b"b\n"
    assert recent_writes(db) == []


def test_atomic_write_keeps_the_file_mode(note: Path) -> None:
    note.chmod(0o640)

    atomic_write(note, b"new\n")

    assert note.read_bytes() == b"new\n"
    assert note.stat().st_mode & 0o777 == 0o640


def test_read_for_edit_refuses_a_file_that_changes_mid_read(note: Path) -> None:
    stats = iter([1, 2])

    class FakeStat:
        @property
        def st_mtime_ns(self) -> int:
            return next(stats)

    with patch.object(Path, "stat", return_value=FakeStat()):
        with pytest.raises(WriteError) as exc_info:
            read_for_edit(note)

    assert str(exc_info.value) == "a.md changed while it was read. Try again."


def test_undo_restores_the_exact_bytes_and_is_logged(db: Path, note: Path) -> None:
    note.write_bytes(b"crlf\r\nno newline at end")
    pending = plan_write(db, "test", "Change", [_edit(note, "new\n", "a.md")])
    written = apply_write(db, pending.id, now=T0)

    undone = undo_write(db, written.id, now=T0.replace(hour=13))

    assert undone == WriteOutcome(id=undone.id, status="undone", message="Undone: Change")
    assert note.read_bytes() == b"crlf\r\nno newline at end"
    assert [(w.id, w.summary, w.written_at, w.undone) for w in recent_writes(db)] == [
        (undone.id, "Undo: Change", T0.replace(hour=13, tzinfo=UTC), False),
        (written.id, "Change", T0.replace(tzinfo=UTC), True),
    ]


def test_undo_twice_is_refused(db: Path, note: Path) -> None:
    written = apply_write(db, plan_write(db, "test", "s", [_edit(note, "x\n")]).id)
    undo_write(db, written.id)

    with pytest.raises(WriteError) as exc_info:
        undo_write(db, written.id)

    assert str(exc_info.value) == "This write was already undone."


def test_undo_of_an_undo_redoes_the_change(db: Path, note: Path) -> None:
    written = apply_write(db, plan_write(db, "test", "s", [_edit(note, "x\n")]).id)
    undone = undo_write(db, written.id)

    undo_write(db, undone.id)

    assert note.read_bytes() == b"x\n"


def test_undo_is_refused_when_the_file_changed_after_the_write(db: Path, note: Path) -> None:
    written = apply_write(db, plan_write(db, "test", "s", [_edit(note, "x\n", "a.md")]).id)
    note.write_bytes(b"edited in Obsidian\n")

    outcome = undo_write(db, written.id)

    assert outcome == WriteOutcome(
        id=written.id,
        status="refused",
        message="a.md changed after this write, so it was not undone. Undo the later writes first.",
    )
    assert note.read_bytes() == b"edited in Obsidian\n"
    assert recent_writes(db)[0].undone is False


def test_each_of_the_last_20_writes_undoes_to_its_exact_bytes(db: Path, note: Path) -> None:
    states = [note.read_bytes()]
    ids = []
    for i in range(LOG_KEEP + 1):
        pending = plan_write(db, "test", f"w{i}", [_edit(note, f"version {i}\n")])
        ids.append(apply_write(db, pending.id).id)
        states.append(note.read_bytes())

    assert LOG_KEEP == 20
    assert [w.summary for w in recent_writes(db)] == [f"w{i}" for i in range(20, 0, -1)]
    # Undo newest first: each one puts back the bytes from just before it.
    for i in range(LOG_KEEP, 0, -1):
        assert undo_write(db, ids[i]).status == "undone"
        assert note.read_bytes() == states[i]
    with pytest.raises(WriteError) as exc_info:
        undo_write(db, ids[0])
    assert str(exc_info.value) == "Only the last 20 writes can be undone."


def test_recent_writes_without_a_database_is_empty(tmp_path: Path) -> None:
    assert recent_writes(tmp_path / "none.duckdb") == []


def test_recent_writes_before_any_write_is_empty(db: Path) -> None:
    with writable(db) as con:
        con.execute("CREATE TABLE other (x INTEGER)")

    assert recent_writes(db) == []


def test_undo_is_refused_when_the_file_was_deleted(db: Path, note: Path) -> None:
    written = apply_write(db, plan_write(db, "test", "s", [_edit(note, "x\n", "a.md")]).id)
    note.unlink()

    assert undo_write(db, written.id).status == "refused"
    assert not note.exists()


# ---------------------------------------------------------------------------
# Link notes (W1, Q10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Body\n", "Body\n\n## Related\n\n- [[E]]\n"),
        ("Body\n\n", "Body\n\n## Related\n\n- [[E]]\n"),
        ("Body", "Body\n\n## Related\n\n- [[E]]"),
        ("", "## Related\n\n- [[E]]\n"),
        ("A\r\nB\r\n", "A\r\nB\r\n\r\n## Related\r\n\r\n- [[E]]\r\n"),
        ("Body\n## Related\n\n- [[Old]]\n", "Body\n## Related\n\n- [[Old]]\n- [[E]]\n"),
        (
            "## Related\n- [[Old]]\n\n## Next\nText\n",
            "## Related\n- [[Old]]\n- [[E]]\n\n## Next\nText\n",
        ),
        ("## Related\n### Sub\nx\n# Top\n", "## Related\n### Sub\nx\n- [[E]]\n# Top\n"),
        ("## Related\n", "## Related\n- [[E]]\n"),
    ],
)
def test_add_related_links(text: str, expected: str) -> None:
    assert add_related_links(text, ["E"]) == expected


def test_link_text_is_the_shortest_name_that_resolves() -> None:
    paths = ["notes/evergreen/E.md", "notes/other/E.md", "notes/evergreen/F.md", "x/e.md"]

    assert link_text("notes/evergreen/F.md", paths) == "F"
    assert link_text("x/e.md", paths) == "e"
    assert link_text("notes/evergreen/E.md", paths) == "evergreen/E"
    assert link_text("notes/other/E.md", ["notes/other/E.md", "notes/other2/E.md", "a/E.md"]) == (
        "other/E"
    )


def test_link_text_falls_back_to_the_full_path() -> None:
    assert link_text("a/b.md", []) == "a/b"


def test_vault_note_accepts_a_note_inside_the_vault(tmp_path: Path) -> None:
    _write(tmp_path, "notes/a.md", "x")

    assert vault_note(tmp_path, "notes/a.md") == (tmp_path / "notes/a.md").resolve()


@pytest.mark.parametrize(
    ("rel", "message"),
    [
        ("../outside.md", "Not a note path: ../outside.md"),
        ("/etc/passwd.md", "Not a note path: /etc/passwd.md"),
        (".kai/topics.md", "Not a note path: .kai/topics.md"),
        ("notes/a.txt", "Not a note path: notes/a.txt"),
        ("notes/missing.md", "No such note: notes/missing.md"),
        ("link.md", "No such note: link.md"),
    ],
)
def test_vault_note_refuses_paths_outside_or_missing(
    tmp_path: Path, rel: str, message: str
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "outside.md").write_text("x")
    (vault / "link.md").symlink_to(tmp_path / "outside.md")

    with pytest.raises(WriteError) as exc_info:
        vault_note(vault, rel)

    assert str(exc_info.value) == message


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write(root, "notes/evergreen/Sleep matters.md", "Evergreen.")
    _write(root, "notes/a.md", "Source A.")
    _write(root, "notes/b.md", "Already [[Sleep matters]].")
    _write(root, "notes/c.md", "Source C.")
    _write(root, "notes/reflections/diary.md", "Private.")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    refresh_notes(root, root / ".kai" / "compass.duckdb", load_topics(root / ".kai/topics.yaml"))
    return root


EVERGREEN = "notes/evergreen/Sleep matters.md"
EXCLUDED = ["notes/reflections"]


def test_plan_links_skips_notes_that_already_link(vault: Path) -> None:
    db = vault / ".kai" / "compass.duckdb"

    edits, summary = plan_links(
        vault, db, EXCLUDED, EVERGREEN, ["notes/a.md", "notes/b.md", "notes/a.md"]
    )

    assert summary == "Link 1 note to [[Sleep matters]]"
    assert [(e.label, e.after) for e in edits] == [
        (
            "notes/a.md",
            b"---\ntitle: a\ntags: [x]\n---\nSource A.\n\n## Related\n\n- [[Sleep matters]]\n",
        )
    ]
    assert edits[0].before == (vault / "notes/a.md").read_bytes()
    assert edits[0].mtime_ns == (vault / "notes/a.md").stat().st_mtime_ns


def test_plan_links_counts_notes_in_the_summary(vault: Path) -> None:
    db = vault / ".kai" / "compass.duckdb"

    _, summary = plan_links(vault, db, EXCLUDED, EVERGREEN, ["notes/a.md", "notes/c.md"])

    assert summary == "Link 2 notes to [[Sleep matters]]"


@pytest.mark.parametrize(
    ("evergreen", "sources", "message"),
    [
        ("notes/a.md", ["notes/c.md"], "Not an evergreen note: notes/a.md"),
        ("notes/none.md", ["notes/c.md"], "Not an evergreen note: notes/none.md"),
        (EVERGREEN, ["notes/b.md"], f"Every note already links to {EVERGREEN}."),
        (EVERGREEN, [EVERGREEN], f"Cannot link this note: {EVERGREEN}"),
        (
            EVERGREEN,
            ["notes/reflections/diary.md"],
            "Cannot link this note: notes/reflections/diary.md",
        ),
        (EVERGREEN, ["../x.md"], "Not a note path: ../x.md"),
    ],
)
def test_plan_links_refusals(vault: Path, evergreen: str, sources: list[str], message: str) -> None:
    with pytest.raises(WriteError) as exc_info:
        plan_links(vault, vault / ".kai" / "compass.duckdb", EXCLUDED, evergreen, sources)

    assert str(exc_info.value) == message


def test_plan_links_refuses_a_note_that_is_not_text(vault: Path) -> None:
    (vault / "notes/c.md").write_bytes(b"\xff\xfe")

    with pytest.raises(WriteError) as exc_info:
        plan_links(vault, vault / ".kai/compass.duckdb", EXCLUDED, EVERGREEN, ["notes/c.md"])

    assert str(exc_info.value) == "Not a UTF-8 text note: notes/c.md"


def test_plan_links_needs_a_scan(tmp_path: Path) -> None:
    with pytest.raises(WriteError) as exc_info:
        plan_links(tmp_path, tmp_path / "none.duckdb", [], EVERGREEN, ["notes/a.md"])

    assert str(exc_info.value) == "No topic data yet. Run `compass scan` first."


def test_link_write_shows_in_the_links_after_a_refresh(vault: Path) -> None:
    db = vault / ".kai" / "compass.duckdb"
    edits, summary = plan_links(vault, db, EXCLUDED, EVERGREEN, ["notes/a.md"])

    apply_write(db, plan_write(db, "link_notes", summary, edits).id)
    refresh_notes(vault, db, load_topics(vault / ".kai/topics.yaml"))

    with pytest.raises(WriteError) as exc_info:
        plan_links(vault, db, EXCLUDED, EVERGREEN, ["notes/a.md"])
    assert str(exc_info.value) == f"Every note already links to {EVERGREEN}."


# ---------------------------------------------------------------------------
# Edit topic (W2, T7)
# ---------------------------------------------------------------------------


def test_new_tags_removes_then_adds_in_order() -> None:
    assert new_tags(["a", "b", "c"], add=["#d", " e ", "d"], remove=["b"]) == ["a", "c", "d", "e"]


@pytest.mark.parametrize(
    ("add", "remove", "message"),
    [
        ([], ["q"], "Not a tag of this topic: q"),
        (["a"], [], "Already a tag of this topic: a"),
        ([], ["a", "b"], "A topic needs at least one tag."),
        ([], [], "Nothing to change."),
        (["  "], ["#"], "Nothing to change."),
    ],
)
def test_new_tags_refusals(add: list[str], remove: list[str], message: str) -> None:
    with pytest.raises(WriteError) as exc_info:
        new_tags(["a", "b"], add=add, remove=remove)

    assert str(exc_info.value) == message


def test_plan_topic_tags_writes_readable_yaml(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(TOPICS_YAML, encoding="utf-8")

    edit, summary = plan_topic_tags(path, "big", add=["sleep"], remove=["x"])

    assert summary == "Topic Big Topic: add sleep, remove x"
    assert edit.label == TOPICS_LABEL == ".kai/topics.yaml"
    assert edit.before == TOPICS_YAML.encode()
    assert edit.after.decode() == (
        "topics:\n"
        "  big:\n    name: Big Topic\n    tags: [y, sleep]\n"
        "  other:\n    name: Other\n    tags: [z]\n"
        "min_notes: 1\ntrend_start: 2026-04-01\n"
        "ai_exclude_folders: [notes/reflections]\n"
    )
    assert path.read_text(encoding="utf-8") == TOPICS_YAML


def test_flow_list_wraps_under_the_first_tag() -> None:
    tags = [f"tag-number-{i:02d}" for i in range(7)]

    assert flow_list(["a"], 10) == "[a]"
    assert flow_list(tags, 10) == (
        "[tag-number-00, tag-number-01, tag-number-02, tag-number-03, tag-number-04,\n"
        "           tag-number-05, tag-number-06]"
    )
    # Exactly at the width fits; one more character wraps.
    assert flow_list(["a" * 80, "b"], 3) == "[" + "a" * 80 + ", b]"
    assert flow_list(["a" * 81, "b"], 3) == "[" + "a" * 81 + ",\n    b]"


HAND_EDITED = (
    "# My topics\n"
    "topics:\n"
    "  big:\n"
    "    name: Big Topic\n"
    "    tags: [x,\n"
    "           y]   # keep these\n"
    "  other:\n"
    "    name: Other\n"
    "    tags:\n"
    "      - z\n"
    "      - w\n"
    "\n"
    "min_notes: 1  # small vault\n"
)


def test_replace_tags_changes_only_that_list() -> None:
    assert replace_tags(HAND_EDITED, "big", ["x", "y", "sleep"]) == HAND_EDITED.replace(
        "[x,\n           y]", "[x, y, sleep]"
    )


def test_replace_tags_turns_a_block_list_into_a_flow_list() -> None:
    assert replace_tags(HAND_EDITED, "other", ["z"]) == HAND_EDITED.replace(
        "      - z\n      - w\n", "      [z]\n"
    )


@pytest.mark.parametrize(
    "text",
    ["topics: [", "topics:\n  big: [1]\n", "other: 1\n", "topics:\n  other:\n    tags: [a]\n"],
)
def test_replace_tags_without_the_list_is_none(text: str) -> None:
    assert replace_tags(text, "big", ["a"]) is None


def test_plan_topic_tags_keeps_comments_and_layout(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(HAND_EDITED, encoding="utf-8")

    edit, _ = plan_topic_tags(path, "other", add=["v"], remove=["w"])

    assert edit.after.decode() == HAND_EDITED.replace("      - z\n      - w\n", "      [z, v]\n")


def test_plan_topic_tags_falls_back_to_a_full_dump_for_a_tag_that_needs_quotes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(HAND_EDITED, encoding="utf-8")

    edit, summary = plan_topic_tags(path, "big", add=["a: b"], remove=[])

    assert summary == "Topic Big Topic: add a: b"
    assert edit.after.decode() == (
        "topics:\n"
        "  big:\n    name: Big Topic\n    tags: [x, y, 'a: b']\n"
        "  other:\n    name: Other\n    tags: [z, w]\n"
        "min_notes: 1\n"
    )
    assert load_topics_text(edit.after) == ["x", "y", "a: b"]


def test_dump_topics_wraps_long_tag_lists() -> None:
    tags = [f"tag-number-{i}" for i in range(8)]
    text = dump_topics({"topics": {"t": {"name": "T", "tags": tags}}})

    assert text == (
        "topics:\n  t:\n    name: T\n"
        "    tags: [tag-number-0, tag-number-1, tag-number-2, tag-number-3, tag-number-4, "
        "tag-number-5,\n      tag-number-6, tag-number-7]\n"
    )


def test_plan_topic_tags_refuses_an_unknown_topic(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(TOPICS_YAML, encoding="utf-8")

    with pytest.raises(WriteError) as exc_info:
        plan_topic_tags(path, "nope", add=["q"], remove=[])

    assert str(exc_info.value) == "Unknown topic: nope"


def test_plan_topic_tags_refuses_an_unreadable_file(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text("topics: [", encoding="utf-8")
    with pytest.raises(TopicsError) as parse_error:
        parse_topics("topics: [")

    with pytest.raises(WriteError) as exc_info:
        plan_topic_tags(path, "big", add=["q"], remove=[])

    assert str(exc_info.value) == f"Cannot read the topics file: {parse_error.value}"


def test_plan_topic_tags_refuses_tags_that_break_the_file(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(TOPICS_YAML, encoding="utf-8")

    with patch("vault_compass.topic_edit.new_tags", return_value=[""]):
        with pytest.raises(WriteError) as exc_info:
            plan_topic_tags(path, "big", add=["q"], remove=[])

    assert str(exc_info.value) == (
        "topics file is invalid: topics.big.tags: Value error, tags must not be blank"
    )


def test_topic_edit_round_trip_through_the_write_layer(tmp_path: Path, db: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(TOPICS_YAML, encoding="utf-8")
    edit, summary = plan_topic_tags(path, "big", add=["sleep"], remove=[])

    written = apply_write(db, plan_write(db, "edit_topic", summary, [edit]).id)

    assert load_topics(path).topics["big"].tags == ["x", "y", "sleep"]
    undo_write(db, written.id)
    assert path.read_text(encoding="utf-8") == TOPICS_YAML


def load_topics_text(data: bytes) -> list[str]:
    return parse_topics(data.decode()).topics["big"].tags


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(vault: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key in ("COMPASS_DB_PATH", "COMPASS_TOPICS_PATH"):
        monkeypatch.delenv(key, raising=False)
    settings = CompassSettings(obsidian_vault_path=vault, openrouter_api_key=None)
    return TestClient(create_app(settings, today=lambda: T0.date()))


def _link_share(api: TestClient) -> float:
    share: float = api.get("/topic-map").json()["tiles"]["link_share"]
    return share


def test_link_flow_preview_apply_and_reread(api: TestClient, vault: Path) -> None:
    before = (vault / "notes/a.md").read_bytes()
    assert _link_share(api) == 0.2

    pending = api.post("/writes/links", json={"evergreen": EVERGREEN, "notes": ["notes/a.md"]})

    assert pending.status_code == 200
    assert pending.json()["summary"] == "Link 1 note to [[Sleep matters]]"
    assert (vault / "notes/a.md").read_bytes() == before
    assert _link_share(api) == 0.2

    applied = api.post(f"/writes/{pending.json()['id']}/apply")

    assert applied.status_code == 200
    assert applied.json() == {
        "id": applied.json()["id"],
        "status": "written",
        "message": "Link 1 note to [[Sleep matters]]",
    }
    assert (vault / "notes/a.md").read_bytes() == before + b"\n## Related\n\n- [[Sleep matters]]\n"
    assert _link_share(api) == 0.4

    undone = api.post(f"/writes/{applied.json()['id']}/undo")

    assert undone.json()["status"] == "undone"
    assert (vault / "notes/a.md").read_bytes() == before
    assert _link_share(api) == 0.2
    assert [(w["summary"], w["undone"]) for w in api.get("/writes").json()] == [
        ("Undo: Link 1 note to [[Sleep matters]]", False),
        ("Link 1 note to [[Sleep matters]]", True),
    ]


def test_apply_refused_after_an_edit_in_obsidian(api: TestClient, vault: Path) -> None:
    pending = api.post("/writes/links", json={"evergreen": EVERGREEN, "notes": ["notes/a.md"]})
    (vault / "notes/a.md").write_text("Edited meanwhile.\n", encoding="utf-8")

    response = api.post(f"/writes/{pending.json()['id']}/apply")

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert (vault / "notes/a.md").read_text(encoding="utf-8") == "Edited meanwhile.\n"
    assert api.get("/writes").json() == []


def test_cancelled_or_unknown_writes_cannot_be_applied(api: TestClient) -> None:
    pending = api.post("/writes/links", json={"evergreen": EVERGREEN, "notes": ["notes/a.md"]})

    assert api.delete(f"/writes/{pending.json()['id']}").status_code == 204
    response = api.post(f"/writes/{pending.json()['id']}/apply")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "This change is no longer waiting for approval. Preview it again."
    }


def test_undo_errors_are_409(api: TestClient) -> None:
    response = api.post("/writes/nope/undo")

    assert response.status_code == 409
    assert response.json() == {"detail": "Only the last 20 writes can be undone."}


def test_link_plan_errors_are_400(api: TestClient) -> None:
    response = api.post("/writes/links", json={"evergreen": "notes/a.md", "notes": ["notes/c.md"]})

    assert response.status_code == 400
    assert response.json() == {"detail": "Not an evergreen note: notes/a.md"}
    assert api.post("/writes/links", json={"evergreen": EVERGREEN, "notes": []}).status_code == 422


def test_topic_edit_flow_updates_the_topics_on_the_next_read(api: TestClient, vault: Path) -> None:
    pending = api.post("/topics/big/tags", json={"add": ["sleep"], "remove": ["x"]})

    assert pending.status_code == 200
    assert pending.json()["files"][0]["lines"] == [
        {"op": " ", "text": "    name: Big Topic"},
        {"op": "-", "text": "    tags: [x, y]"},
        {"op": "+", "text": "    tags: [y, sleep]"},
        {"op": " ", "text": "  other:"},
    ]
    assert api.get("/topics").json()["topics"][0]["tags"] == ["x", "y"]

    api.post(f"/writes/{pending.json()['id']}/apply")

    assert api.get("/topics").json()["topics"][0]["tags"] == ["y", "sleep"]
    assert api.get("/topics/unmapped").json() == [{"tag": "x", "notes": 5}]


@pytest.mark.parametrize(
    ("topic", "body", "status", "detail"),
    [
        ("nope", {"add": ["q"]}, 404, "Unknown topic: nope"),
        ("big", {"remove": ["q"]}, 400, "Not a tag of this topic: q"),
    ],
)
def test_topic_edit_errors(
    api: TestClient, topic: str, body: dict[str, list[str]], status: int, detail: str
) -> None:
    response = api.post(f"/topics/{topic}/tags", json=body)

    assert response.status_code == status
    assert response.json() == {"detail": detail}


def test_plan_topic_tags_falls_back_when_the_small_edit_reads_back_differently(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(HAND_EDITED, encoding="utf-8")

    edit, _ = plan_topic_tags(path, "big", add=["'q'"], remove=[])

    assert load_topics_text(edit.after) == ["x", "y", "'q'"]
    assert not edit.after.decode().startswith("# My topics")
