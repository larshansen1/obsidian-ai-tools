"""The one write layer: every change to the vault or the topics file goes through here.

A change is planned first (a preview; nothing is written), then applied on an
approval click. Apply refuses when a file changed since it was read (W4),
writes each file atomically so Obsidian and sync never see half a file (N6),
and logs the exact bytes before and after, so the last 20 writes can be
undone (W5). An undo is itself a logged write.
"""

import difflib
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel

from .db import readonly, writable

LOG_KEEP = 20

# One write at a time, so a double click cannot apply the same change twice.
_WRITE_LOCK = threading.Lock()

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS pending_writes (
        id VARCHAR PRIMARY KEY,
        kind VARCHAR NOT NULL,
        summary VARCHAR NOT NULL,
        created TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_files (
        write_id VARCHAR NOT NULL,
        seq INTEGER NOT NULL,
        path VARCHAR NOT NULL,
        label VARCHAR NOT NULL,
        mtime_ns BIGINT NOT NULL,
        before BLOB NOT NULL,
        after BLOB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS write_log (
        id VARCHAR PRIMARY KEY,
        seq BIGINT NOT NULL,
        kind VARCHAR NOT NULL,
        summary VARCHAR NOT NULL,
        written_at TIMESTAMP NOT NULL,
        undone_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS write_files (
        write_id VARCHAR NOT NULL,
        seq INTEGER NOT NULL,
        path VARCHAR NOT NULL,
        label VARCHAR NOT NULL,
        before BLOB NOT NULL,
        after BLOB NOT NULL
    )
    """,
)


class WriteError(Exception):
    """A change cannot be planned, applied or undone. The message is user-facing."""


@dataclass(frozen=True)
class FileEdit:
    """One planned file change. `mtime_ns` and `before` are what was read."""

    path: Path
    label: str
    before: bytes
    after: bytes
    mtime_ns: int


class DiffLine(BaseModel):
    # "+" added, "-" removed, " " unchanged context, "@" a gap between changes.
    op: Literal["+", "-", " ", "@"]
    text: str


class FilePreview(BaseModel):
    file: str
    lines: list[DiffLine]


class PendingWrite(BaseModel):
    id: str
    kind: str
    summary: str
    files: list[FilePreview]


class WriteOutcome(BaseModel):
    id: str
    status: Literal["written", "refused", "undone"]
    message: str


class LoggedWrite(BaseModel):
    id: str
    kind: str
    summary: str
    written_at: datetime
    files: list[str]
    undone: bool


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).replace(tzinfo=None)


def _ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in _DDL:
        con.execute(ddl)


def read_for_edit(path: Path) -> tuple[bytes, int]:
    """The file's bytes and the mtime they belong to. Refuses a file that changes mid-read."""
    mtime = path.stat().st_mtime_ns
    data = path.read_bytes()
    if path.stat().st_mtime_ns != mtime:
        raise WriteError(f"{path.name} changed while it was read. Try again.")
    return data, mtime


def diff_lines(before: bytes, after: bytes) -> list[DiffLine]:
    """The changed lines with one line of context, as shown before approval."""
    old = before.decode("utf-8", errors="replace").splitlines()
    new = after.decode("utf-8", errors="replace").splitlines()
    out: list[DiffLine] = []
    for line in list(difflib.unified_diff(old, new, n=1, lineterm=""))[2:]:
        if line.startswith("@@"):
            if out:
                out.append(DiffLine(op="@", text=""))
            continue
        op = line[0] if line[:1] in ("+", "-") else " "
        out.append(DiffLine(op=op, text=line[1:]))  # type: ignore[arg-type]
    return out


def plan_write(
    db_path: Path, kind: str, summary: str, edits: list[FileEdit], now: datetime | None = None
) -> PendingWrite:
    """Store a change for approval and return its preview. Nothing is written to the files."""
    write_id = uuid.uuid4().hex
    with writable(db_path) as con:
        _ensure_tables(con)
        con.execute(
            "INSERT INTO pending_writes VALUES (?, ?, ?, ?)", [write_id, kind, summary, _now(now)]
        )
        for seq, e in enumerate(edits):
            con.execute(
                "INSERT INTO pending_files VALUES (?, ?, ?, ?, ?, ?, ?)",
                [write_id, seq, str(e.path), e.label, e.mtime_ns, e.before, e.after],
            )
    return PendingWrite(
        id=write_id,
        kind=kind,
        summary=summary,
        files=[FilePreview(file=e.label, lines=diff_lines(e.before, e.after)) for e in edits],
    )


def _pending(con: duckdb.DuckDBPyConnection, write_id: str) -> tuple[str, str, list[FileEdit]]:
    row = con.execute(
        "SELECT kind, summary FROM pending_writes WHERE id = ?", [write_id]
    ).fetchone()
    if row is None:
        raise WriteError("This change is no longer waiting for approval. Preview it again.")
    files = con.execute(
        "SELECT path, label, before, after, mtime_ns FROM pending_files "
        "WHERE write_id = ? ORDER BY seq",
        [write_id],
    ).fetchall()
    edits = [FileEdit(Path(p), label, bytes(b), bytes(a), int(m)) for p, label, b, a, m in files]
    return row[0], row[1], edits


def _drop_pending(con: duckdb.DuckDBPyConnection, write_id: str) -> None:
    con.execute("DELETE FROM pending_files WHERE write_id = ?", [write_id])
    con.execute("DELETE FROM pending_writes WHERE id = ?", [write_id])


def cancel_write(db_path: Path, write_id: str) -> None:
    """Forget a pending change. Unknown ids are fine: there is nothing to forget."""
    with writable(db_path) as con:
        _ensure_tables(con)
        _drop_pending(con, write_id)


def atomic_write(path: Path, data: bytes) -> None:
    """Replace the file in one step: a reader sees the old bytes or the new, never a mix."""
    # Hidden name in the same folder: same filesystem for os.replace, ignored by Obsidian.
    tmp = path.with_name(f".{path.name}.compass-tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _changed_since_read(edit: FileEdit) -> bool:
    try:
        data, mtime = read_for_edit(edit.path)
    except (OSError, WriteError):
        return True
    return mtime != edit.mtime_ns or data != edit.before


def _write_all(edits: list[FileEdit]) -> None:
    """Write every file, or put back the ones already written and raise."""
    done: list[FileEdit] = []
    for edit in edits:
        try:
            atomic_write(edit.path, edit.after)
        except OSError as err:
            for written in reversed(done):
                atomic_write(written.path, written.before)
            raise WriteError(f"Could not write {edit.label}: {err}. Nothing was changed.") from None
        done.append(edit)


def _log(
    con: duckdb.DuckDBPyConnection, kind: str, summary: str, edits: list[FileEdit], now: datetime
) -> str:
    log_id = uuid.uuid4().hex
    row = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM write_log").fetchone()
    assert row is not None
    con.execute(
        "INSERT INTO write_log VALUES (?, ?, ?, ?, ?, NULL)", [log_id, row[0], kind, summary, now]
    )
    for seq, e in enumerate(edits):
        con.execute(
            "INSERT INTO write_files VALUES (?, ?, ?, ?, ?, ?)",
            [log_id, seq, str(e.path), e.label, e.before, e.after],
        )
    _prune(con)
    return log_id


def _prune(con: duckdb.DuckDBPyConnection) -> None:
    """Keep the last LOG_KEEP writes, and the undos made after the oldest of them.
    Undos do not count, so undoing never pushes a write out of reach.
    """
    row = con.execute(
        "SELECT MIN(seq) FROM (SELECT seq FROM write_log WHERE kind <> 'undo' "
        "ORDER BY seq DESC LIMIT ?)",
        [LOG_KEEP],
    ).fetchone()
    oldest = row[0] if row is not None and row[0] is not None else 0
    con.execute(
        "DELETE FROM write_files WHERE write_id IN (SELECT id FROM write_log WHERE seq < ?)",
        [oldest],
    )
    con.execute("DELETE FROM write_log WHERE seq < ?", [oldest])


def apply_write(db_path: Path, write_id: str, now: datetime | None = None) -> WriteOutcome:
    """Write an approved change, unless a file changed since it was read (W4)."""
    with _WRITE_LOCK, writable(db_path) as con:
        _ensure_tables(con)
        kind, summary, edits = _pending(con, write_id)
        _drop_pending(con, write_id)
        changed = [e.label for e in edits if _changed_since_read(e)]
        if changed:
            return WriteOutcome(
                id=write_id,
                status="refused",
                message=f"{', '.join(changed)} changed since it was read. "
                "Nothing was written. Preview the change again, then retry.",
            )
        _write_all(edits)
        log_id = _log(con, kind, summary, edits, _now(now))
    return WriteOutcome(id=log_id, status="written", message=summary)


def _logged(con: duckdb.DuckDBPyConnection, log_id: str) -> tuple[str, bool, list[FileEdit]]:
    row = con.execute(
        "SELECT summary, undone_at IS NOT NULL FROM write_log WHERE id = ?", [log_id]
    ).fetchone()
    if row is None:
        raise WriteError(f"Only the last {LOG_KEEP} writes can be undone.")
    files = con.execute(
        "SELECT path, label, before, after FROM write_files WHERE write_id = ? ORDER BY seq",
        [log_id],
    ).fetchall()
    edits = [FileEdit(Path(p), label, bytes(b), bytes(a), 0) for p, label, b, a in files]
    return row[0], bool(row[1]), edits


def _current_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def undo_write(db_path: Path, log_id: str, now: datetime | None = None) -> WriteOutcome:
    """Put back the exact bytes from before a logged write, if nothing changed them since."""
    with _WRITE_LOCK, writable(db_path) as con:
        _ensure_tables(con)
        summary, undone, edits = _logged(con, log_id)
        if undone:
            raise WriteError("This write was already undone.")
        changed = [e.label for e in edits if _current_bytes(e.path) != e.after]
        if changed:
            return WriteOutcome(
                id=log_id,
                status="refused",
                message=f"{', '.join(changed)} changed after this write, so it was not undone. "
                "Undo the later writes first.",
            )
        reverse = [FileEdit(e.path, e.label, e.after, e.before, 0) for e in edits]
        _write_all(reverse)
        stamp = _now(now)
        con.execute("UPDATE write_log SET undone_at = ? WHERE id = ?", [stamp, log_id])
        undo_id = _log(con, "undo", f"Undo: {summary}", reverse, stamp)
    return WriteOutcome(id=undo_id, status="undone", message=f"Undone: {summary}")


def recent_writes(db_path: Path) -> list[LoggedWrite]:
    """The logged writes that can still be undone, newest first."""
    if not db_path.exists():
        return []
    with readonly(db_path) as con:
        try:
            rows = con.execute(
                "SELECT w.id, w.kind, w.summary, w.written_at, w.undone_at IS NOT NULL, "
                "list(f.label ORDER BY f.seq) FROM write_log w "
                "JOIN write_files f ON f.write_id = w.id "
                "GROUP BY w.id, w.kind, w.summary, w.written_at, w.undone_at, w.seq "
                "ORDER BY w.seq DESC"
            ).fetchall()
        except duckdb.CatalogException:
            return []
    return [
        LoggedWrite(id=i, kind=k, summary=s, written_at=t, files=list(f), undone=bool(u))
        for i, k, s, t, u, f in rows
    ]
