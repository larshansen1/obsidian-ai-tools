"""Notes table: scan the vault and write one row per .md file to compass.duckdb.

Frontmatter is read with kai's own parser so both tools agree on what a note
is. Hidden folders (any path part starting with ".") are skipped (D1).
Created dates are normalized to a plain date (D3); notes with no date or an
unparseable one are listed in the report rather than dropped.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from obsidian_ai_tools._vault_store import VaultStore

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
    rows: list[NoteRow] = []
    report = ScanReport()
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
    return rows, report


def refresh_notes(vault_path: Path, db_path: Path) -> ScanReport:
    """Rescan the vault and replace the notes table in compass.duckdb."""
    rows, report = scan_vault(vault_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(_NOTES_DDL)
        con.executemany(
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
    finally:
        con.close()
    return report
