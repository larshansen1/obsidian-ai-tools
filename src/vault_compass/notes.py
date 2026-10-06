"""Notes table: scan the vault and write one row per .md file to compass.duckdb.

Frontmatter is read with kai's own parser so both tools agree on what a note
is. Hidden folders (any path part starting with ".") are skipped (D1).
Created dates are normalized to a plain date (D3); notes with no date or an
unparseable one are listed in the report rather than dropped. The same scan
also fills the tag, link and likely-duplicate tables (D2, D5).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from obsidian_ai_tools._vault_store import VaultStore

from .relations import (
    DuplicateRow,
    LinkRow,
    extract_tags,
    find_duplicates,
    link_targets,
    resolve_links,
)
from .topics import TopicsFile

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


@dataclass
class _Scan:
    rows: list[NoteRow] = field(default_factory=list)
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


def _scan(vault_path: Path) -> _Scan:
    scan = _Scan()
    rows, report = scan.rows, scan.report
    for md in sorted(vault_path.rglob("*.md")):
        rel = md.relative_to(vault_path)
        if _is_hidden(rel) or not md.is_file():
            continue
        rel_str = rel.as_posix()
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
    report.note_count = len(rows)
    return scan


def _insert_all(con: duckdb.DuckDBPyConnection, sql: str, params: list[tuple]) -> None:
    # DuckDB rejects executemany with no parameter sets.
    if params:
        con.executemany(sql, params)


def refresh_notes(vault_path: Path, db_path: Path, topics: TopicsFile | None = None) -> ScanReport:
    """Rescan the vault and replace the notes, tag, link and duplicate tables.

    With `topics`, also rebuild topic_tags, the note_topics view and the
    unmapped-tags list.
    """
    scan = _scan(vault_path)
    rows, report = scan.rows, scan.report
    links = resolve_links(scan.targets)
    duplicates = find_duplicates([(r.path, r.title, r.source_url, r.author) for r in rows])
    report.unresolved_links = [link for link in links if not link.is_resolved]
    report.duplicates = duplicates
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        # The view depends on note_tags, so it must go before the tables are replaced.
        con.execute("DROP VIEW IF EXISTS note_topics")
        con.execute("DROP TABLE IF EXISTS topic_tags")
        con.execute(_NOTES_DDL)
        con.execute(_NOTE_TAGS_DDL)
        con.execute(_LINKS_DDL)
        con.execute(_DUPLICATES_DDL)
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
                for r in rows
            ],
        )
        _insert_all(
            con,
            "INSERT INTO note_tags VALUES (?, ?)",
            [(path, tag) for path, tags in scan.tags.items() for tag in tags],
        )
        _insert_all(
            con,
            "INSERT INTO links VALUES (?, ?, ?, ?)",
            [(link.source_path, link.target, link.target_path, link.is_resolved) for link in links],
        )
        _insert_all(
            con,
            "INSERT INTO duplicates VALUES (?, ?, ?)",
            [(d.path_a, d.path_b, d.reason) for d in duplicates],
        )
        if topics is not None:
            con.execute(_TOPIC_TAGS_DDL)
            _insert_all(con, "INSERT INTO topic_tags VALUES (?, ?)", topics.topic_tag_pairs())
            con.execute(_NOTE_TOPICS_VIEW)
            report.unmapped_tags = [
                (t, int(n)) for t, n in con.execute(UNMAPPED_TAGS_SQL).fetchall()
            ]
    finally:
        con.close()
    return report
