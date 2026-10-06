"""Tests for the Vault Compass notes table (D1, D3)."""

from datetime import date
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from vault_compass import cli as compass_cli
from vault_compass.notes import (
    NoteRow,
    ScanReport,
    normalize_created,
    refresh_notes,
    scan_vault,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _write(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# normalize_created
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, (None, True)),
        ("", (None, True)),
        ("   ", (None, True)),
        ("2026-01-02T21:30:10.077404", (date(2026, 1, 2), True)),
        ("2026-01-02", (date(2026, 1, 2), True)),
        ("  2026-01-02  ", (date(2026, 1, 2), True)),
        ("2026/03/04", (date(2026, 3, 4), True)),
        ("04.03.2026", (date(2026, 3, 4), True)),
        ("04-03-2026", (date(2026, 3, 4), True)),
        ("last tuesday", (None, False)),
        ("2026-13-45", (None, False)),
        (12345, (None, False)),
    ],
)
def test_normalize_created_strings_and_missing(value: object, expected: tuple) -> None:
    assert normalize_created(value) == expected


def test_normalize_created_yaml_date_and_datetime_objects() -> None:
    from datetime import datetime

    assert normalize_created(datetime(2026, 5, 6, 23, 59, 59)) == (date(2026, 5, 6), True)
    assert normalize_created(date(2026, 5, 6)) == (date(2026, 5, 6), True)


# ---------------------------------------------------------------------------
# scan_vault
# ---------------------------------------------------------------------------


def test_scan_vault_full_row(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "inbox/a.md",
        "---\ntitle: Alpha\ncreated: 2026-01-02T21:30:10.077404\nsource_type: web\n"
        "source_url: https://x.test/a\nauthor: Ann\n---\none two three\nfour\n",
    )

    rows, report = scan_vault(tmp_path)

    assert rows == [
        NoteRow(
            path="inbox/a.md",
            title="Alpha",
            created=date(2026, 1, 2),
            source_type="web",
            source_url="https://x.test/a",
            author="Ann",
            folder="inbox",
            word_count=4,
            is_evergreen=False,
        )
    ]
    assert report == ScanReport(note_count=1)


def test_scan_vault_defaults_without_frontmatter(tmp_path: Path) -> None:
    _write(tmp_path, "root.md", "just some words here\n")

    rows, report = scan_vault(tmp_path)

    assert rows == [
        NoteRow(
            path="root.md",
            title="root",
            created=None,
            source_type=None,
            source_url=None,
            author=None,
            folder="",
            word_count=4,
            is_evergreen=False,
        )
    ]
    assert report == ScanReport(note_count=1, undated=["root.md"])


def test_scan_vault_skips_hidden_folders_and_files(tmp_path: Path) -> None:
    _write(tmp_path, "keep.md", "x")
    _write(tmp_path, ".obsidian/skip.md", "x")
    _write(tmp_path, "notes/.trash/skip.md", "x")
    _write(tmp_path, ".hidden.md", "x")
    _write(tmp_path, "notes/readme.txt", "x")

    rows, report = scan_vault(tmp_path)

    assert [r.path for r in rows] == ["keep.md"]
    assert report.note_count == 1


def test_scan_vault_reports_unparsed_and_undated_separately(tmp_path: Path) -> None:
    _write(tmp_path, "good.md", "---\ncreated: 2026-02-03\n---\nbody")
    _write(tmp_path, "bad.md", "---\ncreated: not a date\n---\nbody")
    _write(tmp_path, "none.md", "---\ntitle: T\n---\nbody")

    rows, report = scan_vault(tmp_path)

    assert report == ScanReport(
        note_count=3, undated=["none.md"], unparsed_dates=["bad.md"], unreadable=[]
    )
    assert {r.path: r.created for r in rows} == {
        "bad.md": None,
        "good.md": date(2026, 2, 3),
        "none.md": None,
    }


def test_scan_vault_evergreen_flag(tmp_path: Path) -> None:
    _write(tmp_path, "notes/evergreen/e.md", "x")
    _write(tmp_path, "notes/evergreen/sub/e2.md", "x")
    _write(tmp_path, "notes/evergreen-ish/n.md", "x")
    _write(tmp_path, "inbox/n.md", "x")

    rows, _ = scan_vault(tmp_path)

    assert {r.path: r.is_evergreen for r in rows} == {
        "inbox/n.md": False,
        "notes/evergreen-ish/n.md": False,
        "notes/evergreen/e.md": True,
        "notes/evergreen/sub/e2.md": True,
    }


def test_scan_vault_author_list_is_joined_and_blank_values_are_none(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.md",
        '---\nauthor:\n  - Ann\n  - Bob\nsource_url: ""\ntitle: "  "\n---\nx',
    )

    rows, _ = scan_vault(tmp_path)

    assert rows[0].author == "Ann, Bob"
    assert rows[0].source_url is None
    assert rows[0].title == "a"


def test_scan_vault_unreadable_file_still_gets_a_row(tmp_path: Path) -> None:
    (tmp_path / "bin.md").write_bytes(b"\xff\xfe\x00bad")

    rows, report = scan_vault(tmp_path)

    assert [r.path for r in rows] == ["bin.md"]
    assert rows[0].word_count == 0
    assert report.unreadable == ["bin.md"]
    assert report.note_count == 1


def test_scan_vault_yaml_date_values(tmp_path: Path) -> None:
    # Unquoted values are parsed by YAML into date/datetime objects.
    _write(tmp_path, "d.md", "---\ncreated: 2026-04-05\n---\nx")
    _write(tmp_path, "t.md", "---\ncreated: 2026-04-05 10:11:12\n---\nx")

    rows, report = scan_vault(tmp_path)

    assert [r.created for r in rows] == [date(2026, 4, 5), date(2026, 4, 5)]
    assert report == ScanReport(note_count=2)


# ---------------------------------------------------------------------------
# refresh_notes
# ---------------------------------------------------------------------------


def test_refresh_notes_writes_rows_and_replaces_on_rerun(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntitle: A\ncreated: 2026-01-02\n---\none two")
    _write(vault, "notes/evergreen/b.md", "x")
    db = vault / ".kai" / "compass.duckdb"

    first = refresh_notes(vault, db)
    (vault / "a.md").unlink()
    second = refresh_notes(vault, db)

    assert first == ScanReport(note_count=2, undated=["notes/evergreen/b.md"])
    assert second == ScanReport(note_count=1, undated=["notes/evergreen/b.md"])
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT * FROM notes ORDER BY path").fetchall() == [
            ("notes/evergreen/b.md", "b", None, None, None, None, "notes/evergreen", 1, True)
        ]
    finally:
        con.close()


def test_refresh_notes_stores_all_columns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(
        vault,
        "inbox/a.md",
        "---\ntitle: A\ncreated: 2026-01-02\nsource_type: youtube\n"
        "source_url: https://y.test/1\nauthor: Ann\n---\none two",
    )
    db = tmp_path / "out" / "c.duckdb"

    refresh_notes(vault, db)

    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT * FROM notes").fetchall() == [
            (
                "inbox/a.md",
                "A",
                date(2026, 1, 2),
                "youtube",
                "https://y.test/1",
                "Ann",
                "inbox",
                2,
                False,
            )
        ]
    finally:
        con.close()


def test_row_count_equals_md_count_on_test_vault(tmp_path: Path) -> None:
    vault = REPO_ROOT / "test_vault"
    md_count = len(list(vault.rglob("*.md")))
    db = tmp_path / "c.duckdb"

    report = refresh_notes(vault, db)

    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM notes").fetchone() == (md_count,)
    finally:
        con.close()
    assert report.note_count == md_count
    assert report.unparsed_dates == []


# ---------------------------------------------------------------------------
# compass scan
# ---------------------------------------------------------------------------


def test_scan_command_prints_exact_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "vault"
    _write(vault, "ok.md", "---\ncreated: 2026-01-02\n---\nx")
    _write(vault, "bad.md", "---\ncreated: nope\n---\nx")
    _write(vault, "none.md", "x")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    monkeypatch.setattr("obsidian_ai_tools.config.find_env_file", lambda: None)

    result = runner.invoke(compass_cli.app, ["scan"])

    assert result.exit_code == 0
    assert result.output == (
        f"Created {vault.resolve() / '.kai' / 'topics.yaml'} with the starter topics\n"
        "Notes: 3\nUnparsed dates: 1\n  bad.md\nUndated notes: 1\n  none.md\n"
        "Unresolved links: 0\nUnmapped tags: 0\nLikely duplicates: 0\n"
    )
    assert (vault / ".kai" / "compass.duckdb").is_file()


def test_scan_command_lists_unreadable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "bin.md").write_bytes(b"\xff\xfe\x00")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    monkeypatch.setattr("obsidian_ai_tools.config.find_env_file", lambda: None)

    result = runner.invoke(compass_cli.app, ["scan"])

    assert result.output == (
        f"Created {vault.resolve() / '.kai' / 'topics.yaml'} with the starter topics\n"
        "Notes: 1\nUnparsed dates: 0\nUndated notes: 1\n  bin.md\n"
        "Unresolved links: 0\nUnmapped tags: 0\nLikely duplicates: 0\n"
        "Unreadable files: 1\n  bin.md\n"
    )


def test_scan_command_bad_config_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr("obsidian_ai_tools.config.find_env_file", lambda: None)

    result = runner.invoke(compass_cli.app, ["scan"])

    assert result.exit_code == 1
    assert result.output.startswith("❌ Configuration error:\n")
