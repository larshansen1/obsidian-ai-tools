"""Notes table: scan the vault and write one row per .md file to compass.duckdb.

Frontmatter is read with kai's own parser so both tools agree on what a note
is. Hidden folders (any path part starting with ".") are skipped (D1).
Created dates are normalized to a plain date (D3); notes with no date or an
unparseable one are listed in the report rather than dropped. The same scan
also fills the tag, link and likely-duplicate tables (D2, D5).
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from obsidian_ai_tools._vault_store import VaultStore

from .db import writable
from .relations import (
    DuplicateRow,
    LinkRow,
    extract_tags,
    find_duplicates,
    link_targets,
    resolve_links,
)
from .topics import TopicsFile

logger = logging.getLogger(__name__)

EVERGREEN_FOLDER = "notes/evergreen"  # ADR 0004

# Formats tried in order after datetime.fromisoformat, which already handles
# "2026-01-02T21:30:10.077404" and "2026-01-02".
_FALLBACK_DATE_FORMATS = ("%Y/%m/%d", "%d.%m.%Y", "%d-%m-%Y")

_NOTES_DDL = """
CREATE OR REPLACE TABLE notes (
    path VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    created DATE,
    source_type VARCHAR,
    source_url VARCHAR,
    author VARCHAR,
    folder VARCHAR NOT NULL,
    word_count INTEGER NOT NULL,
    is_evergreen BOOLEAN NOT NULL
)
"""

_NOTE_TAGS_DDL = """
CREATE OR REPLACE TABLE note_tags (
    path VARCHAR NOT NULL,
    tag VARCHAR NOT NULL,
    PRIMARY KEY (path, tag)
)
"""

_LINKS_DDL = """
CREATE OR REPLACE TABLE links (
    source_path VARCHAR NOT NULL,
    target VARCHAR NOT NULL,
    target_path VARCHAR,
    is_resolved BOOLEAN NOT NULL
)
"""

_DUPLICATES_DDL = """
CREATE OR REPLACE TABLE duplicates (
    path_a VARCHAR NOT NULL,
    path_b VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    PRIMARY KEY (path_a, path_b)
)
"""

# One row per note file: its mtime (the change check for incremental refresh, D7)
# and its raw link targets, so links can be re-resolved without re-reading the file.
_NOTE_FILES_DDL = """
CREATE OR REPLACE TABLE note_files (
    path VARCHAR PRIMARY KEY,
    mtime_ns BIGINT NOT NULL,
    link_targets VARCHAR[] NOT NULL
)
"""

_REQUIRED_TABLES = frozenset({"notes", "note_tags", "note_files", "links", "duplicates"})

_TOPIC_TAGS_DDL = """
CREATE OR REPLACE TABLE topic_tags (
    topic VARCHAR NOT NULL,
    tag VARCHAR NOT NULL,
    PRIMARY KEY (topic, tag)
)
"""

# A note belongs to every topic whose tags it carries (ADR 0003).
_NOTE_TOPICS_VIEW = """
CREATE OR REPLACE VIEW note_topics AS
SELECT DISTINCT nt.path, tt.topic
FROM note_tags nt
JOIN topic_tags tt ON tt.tag = nt.tag
"""

# Tags that map to no topic, most used first.
UNMAPPED_TAGS_SQL = """
SELECT tag, COUNT(*) AS notes
FROM note_tags
WHERE tag NOT IN (SELECT tag FROM topic_tags)
GROUP BY tag
ORDER BY notes DESC, tag
"""


@dataclass(frozen=True)
class NoteRow:
    path: str
    title: str
    created: date | None
    source_type: str | None
    source_url: str | None
    author: str | None
    folder: str
    word_count: int
    is_evergreen: bool


@dataclass
class ScanReport:
    """Outcome of a scan. Paths are vault-relative, sorted."""

    note_count: int = 0
    undated: list[str] = field(default_factory=list)
    unparsed_dates: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    # Filled by refresh_notes only.
    unresolved_links: list[LinkRow] = field(default_factory=list)
    duplicates: list[DuplicateRow] = field(default_factory=list)
    # (tag, note count); filled only when topics are given.
    unmapped_tags: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class RefreshStats:
    """What an incremental refresh did. `full` means the tables were rebuilt from scratch."""

    note_count: int
    added: int
    updated: int
    removed: int
    full: bool


@dataclass
class _Scan:
    rows: list[NoteRow] = field(default_factory=list)
    # Paths of every visible note mapped to its mtime; rows/tags/targets cover only parsed files.
    mtimes: dict[str, int] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)
    targets: dict[str, list[str]] = field(default_factory=dict)
    report: ScanReport = field(default_factory=ScanReport)


def normalize_created(value: Any) -> tuple[date | None, bool]:
    """Return (date, ok). (None, True) means no date given; (None, False) means unparseable."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, True
    # datetime is a subclass of date, so it must be checked first.
    if isinstance(value, datetime):
        return value.date(), True
    if isinstance(value, date):
        return value, True
    if isinstance(value, str):
        text = value.strip()
        try:
            return datetime.fromisoformat(text).date(), True
        except ValueError:
            pass
        for fmt in _FALLBACK_DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date(), True
            except ValueError:
                continue
    return None, False


def _text(value: Any) -> str | None:
    """Frontmatter value as a clean string; lists (e.g. several authors) are joined."""
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip()
    return text or None


def _is_hidden(rel: Path) -> bool:
    return any(part.startswith(".") for part in rel.parts)


def _is_evergreen(rel: Path) -> bool:
    return rel.parent.as_posix() == EVERGREEN_FOLDER or rel.parent.as_posix().startswith(
        EVERGREEN_FOLDER + "/"
    )


def scan_vault(vault_path: Path) -> tuple[list[NoteRow], ScanReport]:
    """Read every visible .md file under vault_path."""
    scan = _scan(vault_path)
    return scan.rows, scan.report


def _scan(vault_path: Path, known: dict[str, int] | None = None) -> _Scan:
    """Read visible notes. With `known` (path -> mtime_ns), files whose mtime is unchanged are
    not parsed; they still appear in `mtimes` so callers can tell what was deleted.
    """
    scan = _Scan()
    rows, report = scan.rows, scan.report
    for md in sorted(vault_path.rglob("*.md")):
        rel = md.relative_to(vault_path)
        if _is_hidden(rel) or not md.is_file():
            continue
        rel_str = rel.as_posix()
        try:
            mtime_ns = md.stat().st_mtime_ns
        except OSError:
            # Vanished between listing and stat.
            report.unreadable.append(rel_str)
            continue
        scan.mtimes[rel_str] = mtime_ns
        if known is not None and known.get(rel_str) == mtime_ns:
            continue
        try:
            fm, body = VaultStore.parse_frontmatter(md)
        except (OSError, UnicodeDecodeError):
            fm, body = {}, ""
            report.unreadable.append(rel_str)
        scan.tags[rel_str] = extract_tags(fm.get("tags"))
        scan.targets[rel_str] = link_targets(body)
        created, ok = normalize_created(fm.get("created"))
        if not ok:
            report.unparsed_dates.append(rel_str)
        elif created is None:
            report.undated.append(rel_str)
        rows.append(
            NoteRow(
                path=rel_str,
                title=_text(fm.get("title")) or md.stem,
                created=created,
                source_type=_text(fm.get("source_type")),
                source_url=_text(fm.get("source_url")),
                author=_text(fm.get("author")),
                folder=rel.parent.as_posix() if rel.parent != Path(".") else "",
                word_count=len(body.split()),
                is_evergreen=_is_evergreen(rel),
            )
        )
    report.note_count = len(scan.mtimes)
    return scan


def _insert_all(con: duckdb.DuckDBPyConnection, sql: str, params: list[tuple]) -> None:
    # DuckDB rejects executemany with no parameter sets.
    if params:
        con.executemany(sql, params)


def _insert_notes(con: duckdb.DuckDBPyConnection, scan: _Scan) -> None:
    _insert_all(
        con,
        "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                r.path,
                r.title,
                r.created,
                r.source_type,
                r.source_url,
                r.author,
                r.folder,
                r.word_count,
                r.is_evergreen,
            )
            for r in scan.rows
        ],
    )
    _insert_all(
        con,
        "INSERT INTO note_tags VALUES (?, ?)",
        [(path, tag) for path, tags in scan.tags.items() for tag in tags],
    )
    _insert_all(
        con,
        "INSERT INTO note_files VALUES (?, ?, ?)",
        [(path, scan.mtimes[path], targets) for path, targets in scan.targets.items()],
    )


def _rebuild_relations(
    con: duckdb.DuckDBPyConnection,
) -> tuple[list[LinkRow], list[DuplicateRow]]:
    """Recompute links and duplicates from every note. Both depend on the whole vault
    (a new note can resolve an old link), and are cheap next to reading files.
    """
    targets = {
        path: list(t)
        for path, t in con.execute(
            "SELECT path, link_targets FROM note_files ORDER BY path"
        ).fetchall()
    }
    note_rows = con.execute(
        "SELECT path, title, source_url, author FROM notes ORDER BY path"
    ).fetchall()
    links = resolve_links(targets)
    duplicates = find_duplicates(note_rows)
    # Row-by-row inserts are the slow part of a refresh, so only write the difference.
    _sync_rows(
        con,
        "links",
        ("source_path", "target", "target_path", "is_resolved"),
        {(link.source_path, link.target, link.target_path, link.is_resolved) for link in links},
    )
    _sync_rows(
        con,
        "duplicates",
        ("path_a", "path_b", "reason"),
        {(d.path_a, d.path_b, d.reason) for d in duplicates},
    )
    return links, duplicates


def _sync_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    columns: tuple[str, ...],
    wanted: set[tuple],
) -> None:
    """Make `table` hold exactly `wanted`, deleting and inserting only what differs."""
    # Table and column names come from fixed call sites, never from input.
    existing = set(con.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall())
    match = " AND ".join(f"{c} IS NOT DISTINCT FROM ?" for c in columns)
    _insert_all(con, f"DELETE FROM {table} WHERE {match}", sorted(existing - wanted, key=repr))
    marks = ", ".join("?" for _ in columns)
    _insert_all(con, f"INSERT INTO {table} VALUES ({marks})", sorted(wanted - existing, key=repr))


def _write_topics(con: duckdb.DuckDBPyConnection, topics: TopicsFile, report: ScanReport) -> None:
    # The view depends on topic_tags and note_tags; drop it before replacing the table.
    con.execute("DROP VIEW IF EXISTS note_topics")
    con.execute(_TOPIC_TAGS_DDL)
    _insert_all(con, "INSERT INTO topic_tags VALUES (?, ?)", topics.topic_tag_pairs())
    con.execute(_NOTE_TOPICS_VIEW)
    report.unmapped_tags = [(t, int(n)) for t, n in con.execute(UNMAPPED_TAGS_SQL).fetchall()]


def refresh_notes(vault_path: Path, db_path: Path, topics: TopicsFile | None = None) -> ScanReport:
    """Rescan the vault and replace the notes, tag, link and duplicate tables.

    With `topics`, also rebuild topic_tags, the note_topics view and the
    unmapped-tags list.
    """
    scan = _scan(vault_path)
    report = scan.report
    with writable(db_path) as con:
        # The view depends on note_tags, so it must go before the tables are replaced.
        con.execute("DROP VIEW IF EXISTS note_topics")
        con.execute("DROP TABLE IF EXISTS topic_tags")
        con.execute(_NOTES_DDL)
        con.execute(_NOTE_TAGS_DDL)
        con.execute(_NOTE_FILES_DDL)
        con.execute(_LINKS_DDL)
        con.execute(_DUPLICATES_DDL)
        _insert_notes(con, scan)
        links, duplicates = _rebuild_relations(con)
        report.unresolved_links = [link for link in links if not link.is_resolved]
        report.duplicates = duplicates
        if topics is not None:
            _write_topics(con, topics, report)
    return report


def _existing_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {row[0] for row in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}


def refresh_notes_incremental(
    vault_path: Path, db_path: Path, topics: TopicsFile | None = None
) -> RefreshStats:
    """Bring compass.duckdb up to date, re-reading only files whose mtime changed (D7).

    Falls back to a full rebuild when the database is missing or from before
    mtimes were tracked. With `topics=None` the topic tables are left as they are.
    """
    with writable(db_path) as con:
        have_tables = _REQUIRED_TABLES <= _existing_tables(con)
    if not have_tables:
        report = refresh_notes(vault_path, db_path, topics)
        return RefreshStats(report.note_count, report.note_count, 0, 0, full=True)

    with writable(db_path) as con:
        known = {
            path: int(mtime)
            for path, mtime in con.execute("SELECT path, mtime_ns FROM note_files").fetchall()
        }
        scan = _scan(vault_path, known)
        parsed = {r.path for r in scan.rows}
        removed = set(known) - set(scan.mtimes)
        updated = parsed & set(known)
        con.begin()
        try:
            for table in ("notes", "note_tags", "note_files"):
                _delete_paths(con, table, sorted(removed | updated))
            _insert_notes(con, scan)
            _rebuild_relations(con)
            if topics is not None:
                _write_topics(con, topics, ScanReport())
            con.commit()
        except BaseException:
            con.rollback()
            raise
    return RefreshStats(
        note_count=len(scan.mtimes),
        added=len(parsed - set(known)),
        updated=len(updated),
        removed=len(removed),
        full=False,
    )


def _delete_paths(con: duckdb.DuckDBPyConnection, table: str, paths: list[str]) -> None:
    if paths:
        # Table names come from a fixed tuple above, never from input.
        con.executemany(f"DELETE FROM {table} WHERE path = ?", [(p,) for p in paths])
