"""Tests for Vault Compass tags, links and duplicate flags (D2, D5)."""

from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from vault_compass import cli as compass_cli
from vault_compass.notes import refresh_notes
from vault_compass.relations import (
    TITLE_SIMILARITY_THRESHOLD,
    DuplicateRow,
    LinkRow,
    extract_tags,
    find_duplicates,
    link_targets,
    resolve_links,
)

runner = CliRunner()


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


# ---------------------------------------------------------------------------
# extract_tags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ([], []),
        (["ai", "llm"], ["ai", "llm"]),
        (["#ai", " llm "], ["ai", "llm"]),
        (["ai", "ai", "llm"], ["ai", "llm"]),
        (["ai", "", None, "  "], ["ai", "None"]),
        ("ai, llm", ["ai", "llm"]),
        ("ai llm", ["ai", "llm"]),
        ("#ai", ["ai"]),
        (2026, ["2026"]),
    ],
)
def test_extract_tags(value: object, expected: list[str]) -> None:
    assert extract_tags(value) == expected


# ---------------------------------------------------------------------------
# link_targets
# ---------------------------------------------------------------------------


def test_link_targets_cleans_sorts_and_dedupes() -> None:
    body = "[[B]] [[a|alias]] [[B#Heading]] [[c^block]] [[d.md]] [[#only-heading]] [[  e  ]]"

    assert link_targets(body) == ["B", "a", "c", "d", "e"]


def test_link_targets_none() -> None:
    assert link_targets("no links [here]") == []


# ---------------------------------------------------------------------------
# resolve_links
# ---------------------------------------------------------------------------


def test_resolve_links_by_name_path_and_case() -> None:
    notes = {
        "inbox/src.md": ["Target", "sub/Deep", "missing"],
        "notes/target.md": [],
        "notes/sub/deep.md": [],
    }

    assert resolve_links(notes) == [
        LinkRow("inbox/src.md", "Target", "notes/target.md"),
        LinkRow("inbox/src.md", "sub/Deep", "notes/sub/deep.md"),
        LinkRow("inbox/src.md", "missing", None),
    ]


def test_resolve_links_prefers_shortest_path_then_alphabetical() -> None:
    notes = {
        "src.md": ["same", "tie"],
        "a/b/same.md": [],
        "z/same.md": [],
        "m/tie.md": [],
        "n/tie.md": [],
    }

    assert resolve_links(notes) == [
        LinkRow("src.md", "same", "z/same.md"),
        LinkRow("src.md", "tie", "m/tie.md"),
    ]


def test_resolve_links_each_resolved_link_once_per_source() -> None:
    notes = {"src.md": ["Target", "target", "TARGET"], "target.md": []}

    assert resolve_links(notes) == [LinkRow("src.md", "Target", "target.md")]


def test_resolve_links_unresolved_kept_once_case_insensitive() -> None:
    notes = {"src.md": ["Ghost", "ghost"]}

    assert resolve_links(notes) == [LinkRow("src.md", "Ghost", None)]


def test_link_row_is_resolved() -> None:
    assert LinkRow("a.md", "b", "b.md").is_resolved is True
    assert LinkRow("a.md", "b", None).is_resolved is False


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------


def test_duplicates_same_source_url() -> None:
    notes = [
        ("b.md", "Totally different", "https://y.test/1", None),
        ("a.md", "Another title", "https://y.test/1/", "Ann"),
        ("c.md", "Other", "https://y.test/2", None),
    ]

    assert find_duplicates(notes) == [DuplicateRow("a.md", "b.md", "source_url")]


def test_duplicates_blank_source_url_ignored() -> None:
    notes = [("a.md", "A", "  ", None), ("b.md", "B", "  ", None), ("c.md", "C", "", None)]

    assert find_duplicates(notes) == []


def test_duplicates_same_author_near_identical_title() -> None:
    notes = [
        ("a.md", "10X agentic engineering setup", None, "Ann"),
        ("b.md", "10x Agentic Engineering Setup!", None, "ann"),
    ]

    assert find_duplicates(notes) == [DuplicateRow("a.md", "b.md", "author_title")]


def test_duplicates_title_needs_same_author() -> None:
    notes = [
        ("a.md", "10X agentic engineering setup", None, "Ann"),
        ("b.md", "10X agentic engineering setup", None, "Bob"),
        ("c.md", "10X agentic engineering setup", None, None),
        ("d.md", "10X agentic engineering setup", None, ""),
    ]

    assert find_duplicates(notes) == []


def test_duplicates_title_threshold_boundary() -> None:
    # 20-char titles: one differing char gives ratio 0.95; four give 0.8.
    base = "abcdefghijklmnopqrst"
    close = "abcdefghijklmnopqrsx"
    far = "abcdefghijklmnopwxyz"
    assert TITLE_SIMILARITY_THRESHOLD == 0.85

    assert find_duplicates([("a.md", base, None, "A"), ("b.md", close, None, "A")]) == [
        DuplicateRow("a.md", "b.md", "author_title")
    ]
    assert find_duplicates([("a.md", base, None, "A"), ("b.md", far, None, "A")]) == []


def test_duplicates_url_reason_wins_over_title() -> None:
    notes = [
        ("a.md", "Same title here", "https://y.test/1", "Ann"),
        ("b.md", "Same title here", "https://y.test/1", "Ann"),
    ]

    assert find_duplicates(notes) == [DuplicateRow("a.md", "b.md", "source_url")]


def test_duplicates_empty_titles_never_match() -> None:
    notes = [("a.md", "!!!", None, "Ann"), ("b.md", "???", None, "Ann")]

    assert find_duplicates(notes) == []


# ---------------------------------------------------------------------------
# refresh_notes: tables
# ---------------------------------------------------------------------------


def test_refresh_notes_writes_tag_link_and_duplicate_tables(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(
        vault,
        "inbox/one.md",
        "---\ntitle: One\ntags: [ai, '#llm']\nsource_url: https://y.test/1\n---\n"
        "See [[Two]] and [[Two|again]] and [[Ghost]].",
    )
    _write(
        vault,
        "inbox/two.md",
        "---\ntitle: Two\ntags: ai\nsource_url: https://y.test/1\n---\nBack to [[one]].",
    )
    db = tmp_path / "c.duckdb"

    report = refresh_notes(vault, db)

    assert _query(db, "SELECT * FROM note_tags ORDER BY path, tag") == [
        ("inbox/one.md", "ai"),
        ("inbox/one.md", "llm"),
        ("inbox/two.md", "ai"),
    ]
    assert _query(db, "SELECT * FROM links ORDER BY source_path, target") == [
        ("inbox/one.md", "Ghost", None, False),
        ("inbox/one.md", "Two", "inbox/two.md", True),
        ("inbox/two.md", "one", "inbox/one.md", True),
    ]
    assert _query(db, "SELECT * FROM duplicates") == [
        ("inbox/one.md", "inbox/two.md", "source_url")
    ]
    assert report.unresolved_links == [LinkRow("inbox/one.md", "Ghost", None)]
    assert report.duplicates == [DuplicateRow("inbox/one.md", "inbox/two.md", "source_url")]


def test_refresh_notes_replaces_relation_tables_on_rerun(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags: [x]\n---\n[[b]]")
    _write(vault, "b.md", "body")
    db = tmp_path / "c.duckdb"

    refresh_notes(vault, db)
    (vault / "a.md").unlink()
    refresh_notes(vault, db)

    assert _query(db, "SELECT count(*) FROM note_tags") == [(0,)]
    assert _query(db, "SELECT count(*) FROM links") == [(0,)]


def test_the_two_10x_notes_are_flagged(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    url = "https://www.youtube.com/watch?v=QBfXiWvM0qc"
    _write(
        vault,
        "inbox/youtube-agentic-engineering-at-10x.md",
        f"---\ntitle: 'Agentic Engineering at 10X: Context as Code'\nauthor: David Ondrej\n"
        f"source_url: {url}\n---\nx",
    )
    _write(
        vault,
        "inbox/youtube-10xs-agentic-engineering-setup.md",
        f"---\ntitle: '10X''s Agentic Engineering Setup: Multiplayer AI'\nauthor: David Ondrej\n"
        f"source_url: {url}\n---\nx",
    )
    db = tmp_path / "c.duckdb"

    refresh_notes(vault, db)

    assert _query(db, "SELECT * FROM duplicates") == [
        (
            "inbox/youtube-10xs-agentic-engineering-setup.md",
            "inbox/youtube-agentic-engineering-at-10x.md",
            "source_url",
        )
    ]


# ---------------------------------------------------------------------------
# compass scan output
# ---------------------------------------------------------------------------


def test_scan_command_reports_unresolved_links_and_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ncreated: 2026-01-01\nsource_url: u\n---\n[[Ghost]]")
    _write(vault, "b.md", "---\ncreated: 2026-01-01\nsource_url: u\n---\nx")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("COMPASS_DB_PATH", str(tmp_path / "c.duckdb"))

    result = runner.invoke(compass_cli.app, ["scan"])

    assert result.exit_code == 0
    assert result.output == (
        f"Created {vault.resolve() / '.kai' / 'topics.yaml'} with the starter topics\n"
        "Notes: 2\nUnparsed dates: 0\nUndated notes: 0\n"
        "Unresolved links: 1\nUnmapped tags: 0\nLikely duplicates: 1\n"
        "  a.md <-> b.md (source_url)\n"
    )
