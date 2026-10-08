"""Claims across sources and where they agree (T3, T4, Q6, Q7), cached in compass.duckdb.

Claims are pulled from a topic's notes on demand and cached; notes are never
rewritten. A note's claims stay valid while its file mtime (from `note_files`)
is the one they were read at. Questions, stances and shared claims are cached
against a signature of the topic's claim ids, so they refresh only when the
claims change. Reading the cache never calls a model.

Nothing here is refreshed by a notes scan: these tables are only created with
IF NOT EXISTS and written by the claims run.
"""

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal, NamedTuple

import duckdb
from pydantic import BaseModel

from .ai_policy import is_excluded
from .topic_page import NoteRef
from .topics import TopicsFile

Stance = Literal["supporting", "pushing_back", "unrelated"]
# What the user should do next: read notes, make questions and agreement, or sort by the question.
NextStep = Literal["read", "analyse", "sort", "done"]

AGREEMENT_KIND = "agreement"
STANCES_KIND = "stances"
# Work saved part-way through a long step, so a stopped run resumes instead of starting over.
AGREEMENT_PROGRESS_KIND = "agreement_progress"
STANCES_PROGRESS_KIND = "stances_progress"

CLAIMS_DDL = (
    """
    CREATE TABLE IF NOT EXISTS note_claims (
        claim_id VARCHAR PRIMARY KEY,
        path VARCHAR NOT NULL,
        text VARCHAR NOT NULL,
        origin VARCHAR NOT NULL
    )
    """,
    # One row per note read, with the mtime it was read at; a changed file is read again.
    """
    CREATE TABLE IF NOT EXISTS claim_reads (
        path VARCHAR PRIMARY KEY,
        mtime_ns BIGINT NOT NULL
    )
    """,
    # proposals and the chosen question are JSON/text; `signature` is the claim set they came from.
    """
    CREATE TABLE IF NOT EXISTS topic_questions (
        topic VARCHAR PRIMARY KEY,
        proposals VARCHAR NOT NULL,
        signature VARCHAR NOT NULL,
        chosen VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claim_analysis (
        topic VARCHAR NOT NULL,
        kind VARCHAR NOT NULL,
        key VARCHAR NOT NULL,
        signature VARCHAR NOT NULL,
        payload VARCHAR NOT NULL,
        PRIMARY KEY (topic, kind, key)
    )
    """,
)
_TABLES = frozenset({"note_claims", "claim_reads", "topic_questions", "claim_analysis"})

_ELIGIBLE_SQL = """
SELECT n.path, n.title, n.created, f.mtime_ns
FROM note_topics nt
JOIN notes n ON n.path = nt.path
JOIN note_files f ON f.path = n.path
WHERE nt.topic = ? AND NOT n.is_evergreen
ORDER BY n.path
"""
_EVERGREENS_SQL = """
SELECT n.path, n.title, n.created
FROM note_topics nt JOIN notes n ON n.path = nt.path
WHERE nt.topic = ? AND n.is_evergreen
ORDER BY n.path
"""
_READ_SQL = "SELECT path, mtime_ns FROM claim_reads"
_CLAIMS_SQL = "SELECT claim_id, path, text FROM note_claims ORDER BY path, claim_id"
_QUESTIONS_SQL = "SELECT proposals, signature, chosen FROM topic_questions WHERE topic = ?"
_ANALYSIS_SQL = "SELECT key, signature, payload FROM claim_analysis WHERE topic = ? AND kind = ?"
_LINKERS_SQL = "SELECT DISTINCT source_path FROM links WHERE target_path = ?"


class ClaimItem(BaseModel):
    id: str
    text: str
    path: str
    title: str


class SharedClaim(BaseModel):
    text: str
    claims: list[ClaimItem]
    # The evergreen this matches, if any.
    evergreen: NoteRef | None
    # Source notes behind the claim that do not link to the evergreen (T4).
    unlinked_notes: list[NoteRef]


class ClaimsView(BaseModel):
    topic: str
    # Source notes read so far; evergreens and notes in excluded folders are never counted.
    read_notes: int
    pending_notes: int
    claim_count: int
    questions: list[str]
    question: str | None
    supporting: list[ClaimItem]
    pushing_back: list[ClaimItem]
    # Claims the model found unrelated to the question; counted, not shown.
    unrelated: int
    shared: list[SharedClaim]
    # True when a run would do more work: unread notes, or questions/agreement/stances missing.
    stale: bool
    next_step: NextStep


class EligibleNote(NamedTuple):
    path: str
    title: str
    mtime_ns: int


def claim_id(path: str, text: str) -> str:
    return hashlib.sha1(f"{path}\x00{text}".encode(), usedforsecurity=False).hexdigest()[:12]


def signature(claim_ids: Iterable[str]) -> str:
    joined = "\n".join(sorted(claim_ids))
    return hashlib.sha1(joined.encode(), usedforsecurity=False).hexdigest()


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in CLAIMS_DDL:
        con.execute(ddl)


def _has_tables(con: duckdb.DuckDBPyConnection) -> bool:
    rows = con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    return _TABLES <= {name for (name,) in rows}


def eligible_notes(
    con: duckdb.DuckDBPyConnection, topic: str, definitions: TopicsFile
) -> list[EligibleNote]:
    """The topic's source notes a model may read: no evergreens, no excluded folders (N4)."""
    return [
        EligibleNote(path, title, int(mtime))
        for path, title, _created, mtime in con.execute(_ELIGIBLE_SQL, [topic]).fetchall()
        if not is_excluded(path, definitions.ai_exclude_folders)
    ]


def evergreen_notes(
    con: duckdb.DuckDBPyConnection, topic: str, definitions: TopicsFile
) -> list[NoteRef]:
    return [
        NoteRef(path=path, title=title, created=created)
        for path, title, created in con.execute(_EVERGREENS_SQL, [topic]).fetchall()
        if not is_excluded(path, definitions.ai_exclude_folders)
    ]


def _read_map(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return dict(con.execute(_READ_SQL).fetchall()) if _has_tables(con) else {}


def pending_notes(con: duckdb.DuckDBPyConnection, notes: list[EligibleNote]) -> list[EligibleNote]:
    """Notes never read, or changed since they were read."""
    read = _read_map(con)
    return [n for n in notes if read.get(n.path) != n.mtime_ns]


def valid_claims(con: duckdb.DuckDBPyConnection, notes: list[EligibleNote]) -> list[ClaimItem]:
    """Cached claims of notes whose cache is current, ordered by note path."""
    if not _has_tables(con):
        return []
    read = _read_map(con)
    current = {n.path: n for n in notes if read.get(n.path) == n.mtime_ns}
    return [
        ClaimItem(id=cid, text=text, path=path, title=current[path].title)
        for cid, path, text in con.execute(_CLAIMS_SQL).fetchall()
        if path in current
    ]


def load_questions(con: duckdb.DuckDBPyConnection, topic: str) -> tuple[list[str], str, str | None]:
    """(proposals, signature they were made from, chosen question)."""
    if not _has_tables(con):
        return [], "", None
    row = con.execute(_QUESTIONS_SQL, [topic]).fetchone()
    if row is None:
        return [], "", None
    return list(json.loads(row[0])), row[1], row[2]


def load_analysis(
    con: duckdb.DuckDBPyConnection, topic: str, kind: str, key: str
) -> tuple[str, Any] | None:
    """(signature, payload) cached for (topic, kind, key), or None."""
    if not _has_tables(con):
        return None
    for row_key, sig, payload in con.execute(_ANALYSIS_SQL, [topic, kind]).fetchall():
        if row_key == key:
            return sig, json.loads(payload)
    return None


def save_claims(
    con: duckdb.DuckDBPyConnection, note: EligibleNote, claims: list[tuple[str, str]]
) -> None:
    """Replace a note's cached claims. `claims` is (text, origin) pairs."""
    ensure_tables(con)
    con.execute("DELETE FROM note_claims WHERE path = ?", [note.path])
    seen: set[str] = set()
    for text, origin in claims:
        cid = claim_id(note.path, text)
        if cid in seen:
            continue
        seen.add(cid)
        con.execute("INSERT INTO note_claims VALUES (?, ?, ?, ?)", [cid, note.path, text, origin])
    con.execute("INSERT OR REPLACE INTO claim_reads VALUES (?, ?)", [note.path, note.mtime_ns])


def save_proposals(
    con: duckdb.DuckDBPyConnection, topic: str, questions: list[str], sig: str
) -> None:
    ensure_tables(con)
    _, _, chosen = load_questions(con, topic)
    con.execute(
        "INSERT OR REPLACE INTO topic_questions VALUES (?, ?, ?, ?)",
        [topic, json.dumps(questions), sig, chosen],
    )


def save_chosen_question(con: duckdb.DuckDBPyConnection, topic: str, question: str) -> None:
    """Keep the user's pick (or edit) with the topic (Q6); proposals stay as they were."""
    ensure_tables(con)
    proposals, sig, _ = load_questions(con, topic)
    con.execute(
        "INSERT OR REPLACE INTO topic_questions VALUES (?, ?, ?, ?)",
        [topic, json.dumps(proposals), sig, question],
    )


def save_analysis(
    con: duckdb.DuckDBPyConnection, topic: str, kind: str, key: str, sig: str, payload: Any
) -> None:
    ensure_tables(con)
    con.execute(
        "INSERT OR REPLACE INTO claim_analysis VALUES (?, ?, ?, ?, ?)",
        [topic, kind, key, sig, json.dumps(payload)],
    )


def cached_for(
    con: duckdb.DuckDBPyConnection, topic: str, kind: str, key: str, sig: str
) -> Any | None:
    """The cached payload for (topic, kind, key) if it was made from claim set `sig`."""
    cached = load_analysis(con, topic, kind, key)
    return cached[1] if cached is not None and cached[0] == sig else None


def _split_stances(
    claims: list[ClaimItem], stances: dict[str, str]
) -> tuple[list[ClaimItem], list[ClaimItem], int]:
    supporting = [c for c in claims if stances.get(c.id) == "supporting"]
    pushing = [c for c in claims if stances.get(c.id) == "pushing_back"]
    return supporting, pushing, len(claims) - len(supporting) - len(pushing)


def unlinked_supporters(
    con: duckdb.DuckDBPyConnection, evergreen: str, supporters: Iterable[str]
) -> list[str]:
    """Supporting note paths with no link to `evergreen`, straight from the links table."""
    linked = {path for (path,) in con.execute(_LINKERS_SQL, [evergreen]).fetchall()}
    return sorted({p for p in supporters if p != evergreen and p not in linked})


def _shared_view(
    con: duckdb.DuckDBPyConnection,
    shared: list[dict[str, Any]],
    claims: list[ClaimItem],
    evergreens: list[NoteRef],
) -> list[SharedClaim]:
    by_id = {c.id: c for c in claims}
    by_path = {e.path: e for e in evergreens}
    titles = {c.path: c.title for c in claims}
    out = []
    for item in shared:
        members = [by_id[i] for i in item["claim_ids"] if i in by_id]
        if len({m.path for m in members}) < 2:
            continue
        evergreen = by_path.get(item.get("evergreen") or "")
        unlinked = (
            unlinked_supporters(con, evergreen.path, (m.path for m in members)) if evergreen else []
        )
        out.append(
            SharedClaim(
                text=item["text"],
                claims=members,
                evergreen=evergreen,
                unlinked_notes=[NoteRef(path=p, title=titles[p], created=None) for p in unlinked],
            )
        )
    return out


def _next_step(*, pending: int, has_claims: bool, analysed: bool, sorted_: bool) -> NextStep:
    if pending or not has_claims:
        return "read"
    if not analysed:
        return "analyse"
    return "done" if sorted_ else "sort"


def build_claims_view(
    con: duckdb.DuckDBPyConnection, definitions: TopicsFile, topic: str
) -> ClaimsView:
    """What the topic page and the chat card show. Cached data only; never calls a model."""
    notes = eligible_notes(con, topic, definitions)
    claims = valid_claims(con, notes)
    sig = signature(c.id for c in claims)
    unread = pending_notes(con, notes)
    proposals, proposals_sig, chosen = load_questions(con, topic)
    shared_raw = cached_for(con, topic, AGREEMENT_KIND, "", sig)
    stances = cached_for(con, topic, STANCES_KIND, chosen, sig) if chosen else None
    supporting, pushing, unrelated = _split_stances(claims, stances or {})
    shared = _shared_view(con, shared_raw or [], claims, evergreen_notes(con, topic, definitions))
    next_step = _next_step(
        pending=len(unread),
        has_claims=bool(claims),
        analysed=proposals_sig == sig and shared_raw is not None,
        sorted_=chosen is None or stances is not None,
    )
    return ClaimsView(
        topic=topic,
        read_notes=len(notes) - len(unread),
        pending_notes=len(unread),
        claim_count=len(claims),
        questions=proposals if proposals_sig == sig else [],
        question=chosen,
        supporting=supporting,
        pushing_back=pushing,
        unrelated=unrelated if stances is not None else 0,
        shared=shared,
        stale=next_step != "done",
        next_step=next_step,
    )
