"""Claims across sources and where they agree (T3, T4, Q6, Q7), cached in compass.duckdb.

Claims are pulled from a topic's notes on demand and cached; notes are never
rewritten. A note's claims stay valid while its file mtime (from `note_files`)
is the one they were read at. Questions, stances and shared claims are cached
against a signature of the topic's claim ids. When a few notes change, a run
carries the per-claim results over and asks the model only about what is new
(#175). Reading the cache never calls a model.

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

# "_v2": a claim matches only if it makes the evergreen's specific point (#173); "agreement"
# matched anything on the same theme and is redone.
AGREEMENT_KIND = "agreement_v2"
# "_v3": the sort counts only claims about the question's own subject (#169); "_v2" sorts were
# loose (background claims landed on a side) and are redone.
STANCES_KIND = "stances_v3"
# Work saved part-way through a long step, so a stopped run resumes instead of starting over.
AGREEMENT_PROGRESS_KIND = "agreement_progress_v2"
STANCES_PROGRESS_KIND = "stances_progress_v3"
# Per-evergreen matching state, kept across claim sets so a run matches only new claims (#175):
# {"evergreens": {path: {"line", "scanned", "claim_ids", "text", "best", "kept", "shared_text",
# "confirmed"}}}. "claim_ids" are the candidates the chunk pass found; "kept" the confirmed ones.
AGREEMENT_STATE_KIND = "agreement_state_v1"
# The claim ids the saved question proposals were made from, so the change since can be measured.
QUESTIONS_BASE_KIND = "questions_base_v1"
# Questions are suggested again once the claims added plus removed since reach this share.
QUESTIONS_REDO_PERCENT = 10
MAX_EVERGREENS = 40
# A plain-language summary of how the sorted claims split on the chosen question (#169).
SUMMARY_KIND = "split_summary_v1"

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
_MTIMES_SQL = "SELECT path, mtime_ns FROM note_files"


class ClaimItem(BaseModel):
    id: str
    text: str
    path: str
    title: str


# tension: notes that would answer differently (a yes/no question the sort step can use).
# recurring: an idea many notes repeat but never examine. carry_over: does a principle from one
# kind of system hold in another? Only tension questions are sorted into sides.
QuestionKind = Literal["tension", "recurring", "carry_over"]
QUESTION_KINDS: frozenset[str] = frozenset({"tension", "recurring", "carry_over"})


class SuggestedQuestion(BaseModel):
    text: str
    kind: QuestionKind
    # One line on why the question comes from these notes and not from the field in general.
    why: str
    # The claims the question is anchored in, from at least two notes of the topic.
    anchors: list[ClaimItem]


class SharedClaim(BaseModel):
    text: str
    claims: list[ClaimItem]
    # The evergreen this matches, if any.
    evergreen: NoteRef | None
    # Source notes behind the claim that do not link to the evergreen (T4).
    unlinked_notes: list[NoteRef]


class SplitSummary(BaseModel):
    """How the notes split on the chosen question, in plain language (#169)."""

    # One or two sentences: how the notes split and how strong each side is.
    bottom_line: str
    agree: list[str]
    # Where they split and why: what each side rests on.
    split: list[str]
    # What is missing that would settle the question.
    missing: list[str]
    # The notes most worth reading on the question (3 to 5).
    read: list[NoteRef]


class ClaimsView(BaseModel):
    topic: str
    # Source notes read so far; evergreens and notes in excluded folders are never counted.
    read_notes: int
    pending_notes: int
    claim_count: int
    questions: list[SuggestedQuestion]
    question: str | None
    supporting: list[ClaimItem]
    pushing_back: list[ClaimItem]
    # Claims the model found unrelated to the question; counted, not shown.
    unrelated: int
    # The summary of the split; None until the chosen question is sorted and summarised.
    summary: SplitSummary | None
    shared: list[SharedClaim]
    # True when a run would do more work: unread notes, or questions/agreement/stances missing.
    stale: bool
    next_step: NextStep
    # True when the answer shown was made before notes were added or changed; a run updates it.
    answer_outdated: bool = False


class EligibleNote(NamedTuple):
    path: str
    title: str
    mtime_ns: int


def claim_id(path: str, text: str) -> str:
    return hashlib.sha1(f"{path}\x00{text}".encode(), usedforsecurity=False).hexdigest()[:12]


def signature(claim_ids: Iterable[str]) -> str:
    joined = "\n".join(sorted(claim_ids))
    return hashlib.sha1(joined.encode(), usedforsecurity=False).hexdigest()


def offered_evergreens(
    con: duckdb.DuckDBPyConnection, topic: str, definitions: TopicsFile
) -> list[NoteRef]:
    """The evergreens claims are matched against: the first MAX_EVERGREENS by path."""
    return evergreen_notes(con, topic, definitions)[:MAX_EVERGREENS]


def agreement_signature(
    con: duckdb.DuckDBPyConnection, claims: list[ClaimItem], evergreens: list[NoteRef]
) -> str:
    """The claim set plus each offered evergreen's file mtime: an edited evergreen is redone."""
    mtimes = dict(con.execute(_MTIMES_SQL).fetchall())
    marks = (f"evergreen\x00{e.path}\x00{mtimes.get(e.path, 0)}" for e in evergreens)
    return signature([*(c.id for c in claims), *marks])


def questions_kept(base_ids: Iterable[str], claim_ids: Iterable[str]) -> bool:
    """Whether proposals made from `base_ids` still serve the claim set `claim_ids`.

    They do while the claims added plus removed since are under QUESTIONS_REDO_PERCENT of the
    claims they were made from; anchors already resolve against the current claims.
    """
    base, current = set(base_ids), set(claim_ids)
    return len(base ^ current) * 100 < QUESTIONS_REDO_PERCENT * len(base)


def questions_current(
    con: duckdb.DuckDBPyConnection, topic: str, proposals_sig: str, claims: list[ClaimItem]
) -> bool:
    """Whether the saved proposals stand for `claims`: made from them, or from a set close by."""
    ids = [c.id for c in claims]
    if proposals_sig == signature(ids):
        return True
    base = load_analysis(con, topic, QUESTIONS_BASE_KIND, "")
    return base is not None and base[0] == proposals_sig and questions_kept(base[1], ids)


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


def load_questions(
    con: duckdb.DuckDBPyConnection, topic: str
) -> tuple[list[dict[str, Any]], str, str | None]:
    """(proposals, signature they were made from, chosen question).

    Each proposal is {"text", "kind", "why", "claim_ids"}. Proposals saved in the old format
    (plain strings) come back as none with an empty signature, so the next run suggests again.
    """
    if not _has_tables(con):
        return [], "", None
    row = con.execute(_QUESTIONS_SQL, [topic]).fetchone()
    if row is None:
        return [], "", None
    proposals = json.loads(row[0])
    if not all(isinstance(p, dict) for p in proposals):
        return [], "", row[2]
    return list(proposals), row[1], row[2]


def question_views(
    proposals: list[dict[str, Any]], claims: list[ClaimItem]
) -> list[SuggestedQuestion]:
    """Proposals with their anchor ids resolved to the topic's current claims.

    Anchors that are no longer current are dropped; a question left anchored in fewer than two
    notes is not shown, since it no longer points at notes that bear on it.
    """
    by_id = {c.id: c for c in claims}
    out = []
    for p in proposals:
        anchors = [by_id[i] for i in p.get("claim_ids", []) if i in by_id]
        kind = p.get("kind")
        if (
            len({a.path for a in anchors}) < 2
            or not isinstance(kind, str)
            or kind not in QUESTION_KINDS
        ):
            continue
        out.append(
            SuggestedQuestion(text=p["text"], kind=p["kind"], why=p.get("why", ""), anchors=anchors)
        )
    return out


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
    con: duckdb.DuckDBPyConnection,
    topic: str,
    questions: list[dict[str, Any]],
    claim_ids: list[str],
) -> None:
    """Save proposals with the claim set they were made from (its signature and its ids)."""
    ensure_tables(con)
    _, _, chosen = load_questions(con, topic)
    sig = signature(claim_ids)
    con.execute(
        "INSERT OR REPLACE INTO topic_questions VALUES (?, ?, ?, ?)",
        [topic, json.dumps(questions), sig, chosen],
    )
    save_analysis(con, topic, QUESTIONS_BASE_KIND, "", sig, sorted(claim_ids))


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


def summary_view(raw: dict[str, Any], claims: list[ClaimItem]) -> SplitSummary:
    """A cached summary with its note picks resolved to current notes (gone ones dropped)."""
    by_id = {c.id: c for c in claims}
    read: dict[str, NoteRef] = {}
    for cid in raw.get("read_ids", []):
        claim = by_id.get(cid)
        if claim is not None and claim.path not in read:
            read[claim.path] = NoteRef(path=claim.path, title=claim.title, created=None)
    return SplitSummary(
        bottom_line=raw.get("bottom_line", ""),
        agree=list(raw.get("agree", [])),
        split=list(raw.get("split", [])),
        missing=list(raw.get("missing", [])),
        read=list(read.values()),
    )


class _Answer(NamedTuple):
    """What to show for the chosen question, and whether it is the current claim set's."""

    stances: dict[str, str] | None
    summary: dict[str, Any] | None
    # Both were made from the current claim set, so the sort step has nothing left to do.
    current: bool


def _saved_answer(
    con: duckdb.DuckDBPyConnection, topic: str, chosen: str | None, sig: str
) -> _Answer:
    """The answer for `chosen`: the current one, else the last one saved for it.

    New or changed notes move the claim set on, but the last summary stays readable until a
    run replaces it. Without a saved summary only current stances count, as before.
    """
    if chosen is None:
        return _Answer(None, None, True)
    stances = load_analysis(con, topic, STANCES_KIND, chosen)
    summary = load_analysis(con, topic, SUMMARY_KIND, chosen)
    current = stances is not None and summary is not None and stances[0] == summary[0] == sig
    if summary is None:
        fresh = stances[1] if stances is not None and stances[0] == sig else None
        return _Answer(fresh, None, False)
    return _Answer(stances[1] if stances is not None else None, summary[1], current)


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
    has_questions = questions_current(con, topic, proposals_sig, claims)
    evergreens = evergreen_notes(con, topic, definitions)
    agreement_sig = agreement_signature(con, claims, evergreens[:MAX_EVERGREENS])
    shared_raw = cached_for(con, topic, AGREEMENT_KIND, "", agreement_sig)
    stances, summary, answer_current = _saved_answer(con, topic, chosen or None, sig)
    # Only claims that were sorted count: a claim read since the answer is not "unrelated".
    sorted_claims = [c for c in claims if c.id in stances] if stances is not None else claims
    supporting, pushing, unrelated = _split_stances(sorted_claims, stances or {})
    shared = _shared_view(con, shared_raw or [], claims, evergreens)
    next_step = _next_step(
        pending=len(unread),
        has_claims=bool(claims),
        analysed=has_questions and shared_raw is not None,
        sorted_=answer_current,
    )
    return ClaimsView(
        topic=topic,
        read_notes=len(notes) - len(unread),
        pending_notes=len(unread),
        claim_count=len(claims),
        questions=question_views(proposals, claims) if has_questions else [],
        question=chosen,
        supporting=supporting,
        pushing_back=pushing,
        unrelated=unrelated if stances is not None else 0,
        summary=summary_view(summary, claims) if summary is not None else None,
        shared=shared,
        stale=next_step != "done",
        next_step=next_step,
        answer_outdated=summary is not None and (not answer_current or bool(unread)),
    )
