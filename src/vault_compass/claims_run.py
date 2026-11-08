"""The model steps behind the claims screen (T3, T4, Q6, Q7).

One run reads the topic's unread notes, then asks for question proposals, shared
claims and stances, skipping every step whose cached result is still current.
When only a few notes changed, earlier per-claim results are carried over and
only the new claims go to the model (#175).
Each model call is checked against the cost limits first (N3): over the limit
and not approved, the run stops and reports it; what was done stays cached.
Note text reaches the model only through `AiVault.body` (N4).
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from .ai_client import AiNotConfiguredError, ChatModel, TextDelta, Usage
from .ai_cost import check_limits, estimate_cost, month_spend, record_call
from .claims import (
    AGREEMENT_KIND,
    AGREEMENT_PROGRESS_KIND,
    AGREEMENT_STATE_KIND,
    QUESTION_KINDS,
    QUESTIONS_BASE_KIND,
    STANCES_KIND,
    STANCES_PROGRESS_KIND,
    SUMMARY_KIND,
    ClaimItem,
    EligibleNote,
    agreement_signature,
    cached_for,
    eligible_notes,
    load_analysis,
    load_questions,
    offered_evergreens,
    pending_notes,
    questions_current,
    save_analysis,
    save_claims,
    save_proposals,
    signature,
    valid_claims,
)
from .config import CompassSettings
from .db import readonly, writable
from .topic_page import NoteRef
from .vault_tools import AiVault

logger = logging.getLogger(__name__)

MAX_NOTES_PER_RUN = 30
NOTE_CHARS = 3000
BATCH_CHARS = 12000
MAX_NOTES_PER_BATCH = 4
MAX_CLAIMS_PER_NOTE = 4
MAX_CLAIM_CHARS = 300
# The questions step sees every claim of the topic, grouped by note, so it can find two specific
# notes that disagree. A sample hid most of them (#166). Over this size the claims are thinned
# evenly; chunking would not help, since a tension between two notes can span any two chunks.
MAX_QUESTION_PROMPT_CHARS = 240_000
CHUNK_CLAIMS = 150
# Sorting needs more care per claim than matching, so it takes smaller chunks.
STANCE_CHUNK_CLAIMS = 75
# Per evergreen and chunk: the closest matches only, which keeps the thinking answer short (#173).
MAX_MATCHES_PER_CHUNK = 6
# After all chunks, one call per evergreen keeps only the candidates that really make its point.
MAX_SHARED_CLAIMS = 10
EVERGREEN_CHARS = 300
MAX_QUESTIONS = 6
MAX_WHY_CHARS = 300
KEY_CLAIMS_ORIGIN = "key_claims"
MODEL_ORIGIN = "model"

RunStatus = Literal["done", "needs_approval", "not_configured", "failed"]

EXTRACT_SYSTEM = f"""\
You read notes and list the claims each one makes. A claim is one self-contained statement \
the source asserts: a finding, a recommendation or a position. Give 1 to {MAX_CLAIMS_PER_NOTE} \
claims per note, each under 160 characters, in your own words. Reply with JSON only: \
{{"claims": [{{"note": "<note path exactly as given>", "text": "<claim>"}}]}}"""

QUESTIONS_SYSTEM = f"""\
You help one person think with their own notes. You are given the claims from the notes they \
saved on one topic, grouped by note: a line "N<i> <title>" and then that note's claims, each \
with a number.

Find the questions worth thinking about that come from THIS collection, not from the field in \
general. Return only as many as are really there (0 to {MAX_QUESTIONS}). Fewer strong questions \
beat a full list. Each question must be anchored in claims from at least two different notes and \
be one of two kinds:
- tension: notes make claims about the SAME thing and would answer differently. Phrase it as \
one yes or no question about one position, so some claims support a yes and others push back \
with a no. Good: "Should coffee beans be used within four weeks?". Bad: "Is it A, or B?".
- recurring: one idea keeps coming back across many notes but is never examined head on.

Optionally, at most ONE carry_over question: a principle from one kind of system (for example \
software) and whether it carries over to a different kind (for example a body or a \
relationship). Frame it only as "does this carry over, and what changes?", never as a \
contradiction. Skip it if the link is loose. Most topics should have none.

Rules:
- Both sides of a tension must be about the same subject. A loose analogy is not a tension.
- No textbook debates that would appear in any article on the topic.
- "why" is one plain line on why this question is specific to these notes.
- "claims" lists the numbers of the claims the question is anchored in. Use only numbers you \
were given.
Reply with JSON only: {{"questions": [{{"text": "...", "kind": "tension|recurring|carry_over", \
"why": "...", "claims": [<numbers>]}}]}}"""

AGREEMENT_SYSTEM = f"""\
You are given one of the user's evergreen notes (title | start) and numbered claims from their \
notes (number | note | text). First name the evergreen's specific point in a few words. A claim \
matches only if it makes that point: it restates it, or gives direct evidence or an example of \
that point itself. A claim that is only on the same subject, or supports a different point \
about it, does not match. Most claims do not match. Give one short line stating what the \
matching claims share, and the numbers of at most {MAX_MATCHES_PER_CHUNK} matching claims, \
closest first; an empty list when none match. Use only numbers you were given. Reply with JSON \
only: {{"point": "...", "text": "...", "c": [<claim numbers>]}}"""

CONFIRM_SYSTEM = f"""\
You are given one of the user's evergreen notes (title | start) and numbered candidate claims \
from their notes (number | note | text) that may make its point. First name the evergreen's \
specific point in a few words. Keep only the claims that make that point: they restate it, or \
give direct evidence or an example of that point itself. Drop claims that are only on the same \
subject, or that make a different point. It is fine to keep none. Give one short line stating \
what the kept claims share, and the numbers of at most {MAX_SHARED_CLAIMS} kept claims, closest \
first. Use only numbers you were given. Reply with JSON only: \
{{"point": "...", "text": "...", "c": [<claim numbers>]}}"""

STANCE_SYSTEM = """\
You are given a question and numbered claims. First name the question's subject: the specific \
thing it asks about, in a few words (for a question on one symptom, that symptom, not the \
condition it belongs to). A claim counts only if it is about that subject itself and bears on \
the answer. A claim about the wider field, or one that only shares a theme with the question, \
is unrelated even if it leans one way. Most claims are usually unrelated. List the numbers of \
the claims that count and support a yes, and of those that count and push back (point to a \
no). Leave out every other claim. Use only the numbers you were given. Reply with JSON only: \
{"subject": "<the subject>", "supporting": [<numbers>], "pushing_back": [<numbers>]}"""

SUMMARY_SYSTEM = """\
You help one person think with their own notes. You are given a question and the claims from \
their notes that bear on it, numbered, each with its note title: first the claims that support \
a yes (FOR), then those that push back (AGAINST). Summarise how the notes split, for a smart \
reader who is not an expert. Plain words, short sentences, no jargon, no em dashes.

- bottom_line: one or two sentences. How the notes split and how strong each side is. Say \
plainly when a side has few claims, or when most claims come from one or two notes.
- agree: what both sides accept, as short points (may be empty).
- split: where they part and why: what each side rests on (studies, lived experience, theory).
- missing: what is not in these notes that would settle the question.
- read: the numbers of 3 to 5 claims whose notes are most worth reading, from both sides.
Use only what the claims say. Reply with JSON only: {"bottom_line": "...", "agree": ["..."], \
"split": ["..."], "missing": ["..."], "read": [<numbers>]}"""

_KEY_CLAIMS_HEADING = re.compile(r"^#{1,6}\s*key claims\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_BULLET = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+(.*\S)")


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    message: str | None = None
    estimate_usd: float | None = None


UNREADABLE = "The model's answer could not be read. Try again."
EMPTY_ANSWER = (
    "The model sent an empty answer. A thinking model can use all its output on thinking: "
    "raise COMPASS_AI_CLAIMS_MAX_OUTPUT_TOKENS or pick another model with LLM_MODEL."
)


def _unreadable_message(text: str) -> str:
    return UNREADABLE if text.strip() else EMPTY_ANSWER


class _Stop(Exception):
    def __init__(self, outcome: RunOutcome) -> None:
        super().__init__(outcome.message)
        self.outcome = outcome


class _Unreadable(_Stop):
    """The model answered, but not with usable JSON (often a reply cut off at the token limit)."""


def key_claims(body: str) -> list[str]:
    """Bullets under a "Key Claims" heading; the note already states its claims (Q7)."""
    heading = _KEY_CLAIMS_HEADING.search(body)
    if heading is None:
        return []
    rest = body[heading.end() :]
    following = _NEXT_HEADING.search(rest)
    section = rest[: following.start()] if following else rest
    found = (_BULLET.fullmatch(line) for line in section.splitlines())
    return [m.group(1)[:MAX_CLAIM_CHARS] for m in found if m]


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model answer, tolerating a code fence around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in the answer")
    data: dict[str, Any] = json.loads(text[start : end + 1])
    return data


def _items(value: Any) -> list[Any]:
    """`value` if the model sent a list; anything else (a number, a string, a dict) is no list."""
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return value.strip()[:MAX_CLAIM_CHARS] if isinstance(value, str) else ""


def parse_extracted(data: dict[str, Any], paths: set[str]) -> dict[str, list[str]]:
    """Claims per note path. Claims naming a note outside the batch are dropped."""
    out: dict[str, list[str]] = {}
    for item in _items(data.get("claims")):
        if not isinstance(item, dict):
            continue
        note, text = item.get("note"), _text(item.get("text"))
        if not isinstance(note, str) or note not in paths or not text:
            continue
        bucket = out.setdefault(note, [])
        if len(bucket) < MAX_CLAIMS_PER_NOTE:
            bucket.append(text)
    return out


def _parse_question(value: Any, claims: list[ClaimItem]) -> dict[str, Any] | None:
    """One proposal, or None when it is unusable or anchored in fewer than two notes."""
    if not isinstance(value, dict):
        return None
    text = _text(value.get("text"))
    raw_kind = value.get("kind")
    # Only a string can be a kind; a list or dict would not even be hashable for the lookup.
    kind = raw_kind.strip().lower().replace("-", "_") if isinstance(raw_kind, str) else ""
    anchors = [claims[n - 1] for n in _numbers(value.get("claims"), len(claims))]
    if not text or kind not in QUESTION_KINDS or len({a.path for a in anchors}) < 2:
        return None
    why = value.get("why")
    why = why.strip()[:MAX_WHY_CHARS] if isinstance(why, str) else ""
    return {"text": text, "kind": kind, "why": why, "claim_ids": [a.id for a in anchors]}


def parse_questions(data: dict[str, Any], claims: list[ClaimItem]) -> list[dict[str, Any]]:
    """Usable proposals, at most MAX_QUESTIONS, unique by text, with at most one carry_over.

    `claims` are the claims in the order they were numbered in the prompt.
    """
    out: list[dict[str, Any]] = []
    for value in _items(data.get("questions")):
        question = _parse_question(value, claims)
        if question is None or any(q["text"] == question["text"] for q in out):
            continue
        if question["kind"] == "carry_over" and any(q["kind"] == "carry_over" for q in out):
            continue
        out.append(question)
    return out[:MAX_QUESTIONS]


def _numbers(value: Any, limit: int) -> list[int]:
    """The distinct integers in `value` that fall in 1..limit, in order."""
    found = [n for n in _items(value) if isinstance(n, int) and not isinstance(n, bool)]
    return list(dict.fromkeys(n for n in found if 1 <= n <= limit))


def parse_matches(
    data: dict[str, Any], chunk: list[ClaimItem], evergreen: NoteRef
) -> list[tuple[str, str, list[str]]]:
    """The one evergreen's match as [(path, shared point, claim ids)], or [] when none match.

    Out-of-range numbers are dropped and at most MAX_MATCHES_PER_CHUNK claims are kept.
    """
    ids = [chunk[n - 1].id for n in _numbers(data.get("c"), len(chunk))][:MAX_MATCHES_PER_CHUNK]
    text = _text(data.get("text"))
    return [(evergreen.path, text, ids)] if ids and text else []


def merge_matches(
    found: dict[str, dict[str, Any]], matches: list[tuple[str, str, list[str]]]
) -> None:
    """Fold matches into one entry per evergreen, keeping the wording of the biggest match."""
    for path, text, ids in matches:
        entry = found.setdefault(path, {"text": text, "best": 0, "claim_ids": []})
        if len(ids) > entry["best"]:
            entry["text"], entry["best"] = text, len(ids)
        entry["claim_ids"] = list(dict.fromkeys(entry["claim_ids"] + ids))


def finalize_shared(
    found: dict[str, dict[str, Any]], claims: list[ClaimItem]
) -> list[dict[str, Any]]:
    """Shared claims: evergreens matched by claims from 2 or more different notes."""
    path_of = {c.id: c.path for c in claims}
    shared = [
        {"text": e["text"], "claim_ids": e["claim_ids"], "evergreen": path}
        for path, e in found.items()
        if len({path_of[i] for i in e["claim_ids"] if i in path_of}) >= 2
    ]
    return sorted(shared, key=lambda item: (-len(item["claim_ids"]), item["evergreen"]))


MAX_SUMMARY_POINTS = 6
MAX_SUMMARY_READS = 5
MAX_SUMMARY_CHARS = 600


def _points(value: Any) -> list[str]:
    """Non-blank strings from a list, cut to length, at most MAX_SUMMARY_POINTS."""
    texts = (v.strip()[:MAX_SUMMARY_CHARS] for v in _items(value) if isinstance(v, str))
    return [t for t in texts if t][:MAX_SUMMARY_POINTS]


def parse_summary(data: dict[str, Any], claims: list[ClaimItem]) -> dict[str, Any] | None:
    """The summary to cache, or None when it has no bottom line. `claims` as numbered."""
    bottom = data.get("bottom_line")
    if not isinstance(bottom, str) or not bottom.strip():
        return None
    picks = [claims[n - 1] for n in _numbers(data.get("read"), len(claims))]
    read_ids = list({c.path: c.id for c in reversed(picks)}.values())[::-1]
    return {
        "bottom_line": bottom.strip()[:MAX_SUMMARY_CHARS],
        "agree": _points(data.get("agree")),
        "split": _points(data.get("split")),
        "missing": _points(data.get("missing")),
        "read_ids": read_ids[:MAX_SUMMARY_READS],
    }


def parse_stances(data: dict[str, Any], chunk: list[ClaimItem]) -> dict[str, str]:
    """One stance per claim from the two number lists; unlisted or doubly listed is unrelated."""
    supporting = set(_numbers(data.get("supporting"), len(chunk)))
    pushing = set(_numbers(data.get("pushing_back"), len(chunk)))
    stances = {}
    for n, claim in enumerate(chunk, start=1):
        if n in supporting and n not in pushing:
            stances[claim.id] = "supporting"
        elif n in pushing and n not in supporting:
            stances[claim.id] = "pushing_back"
        else:
            stances[claim.id] = "unrelated"
    return stances


@dataclass(frozen=True)
class _Snapshot:
    pending: list[EligibleNote]
    claims: list[ClaimItem]
    # The evergreens claims are matched against (at most MAX_EVERGREENS).
    evergreens: list[NoteRef]
    sig: str
    # The claim set plus the evergreens' mtimes: agreement is current only for both.
    agreement_sig: str
    has_questions: bool
    # Proposals made from exactly this claim set before their claim ids were stored.
    needs_questions_base: bool
    chosen: str | None
    has_agreement: bool
    has_stances: bool
    has_summary: bool


class _Run:
    """One claims run: asks the model, enforcing the cost limits before every call."""

    def __init__(
        self,
        settings: CompassSettings,
        vault: AiVault,
        model_factory: Callable[[CompassSettings], ChatModel],
        topic: str,
        approved: bool,
        thinking: CompassSettings | None = None,
    ) -> None:
        self.settings = settings
        # Settings for the one step that thinks first (questions); the others use `settings`.
        self.thinking = thinking or settings
        self.vault = vault
        self.topic = topic
        self._factory = model_factory
        self._approved = approved
        self._models: dict[bool, ChatModel] = {}
        self._month: float | None = None
        self._spent = 0.0

    async def ask(self, system: str, user: str, *, think: bool = False) -> dict[str, Any]:
        estimate = estimate_cost(self.thinking if think else self.settings, len(system) + len(user))
        if self._month is None:
            self._month = await asyncio.to_thread(month_spend, self.settings.compass_db_path)
        limit = check_limits(
            self.settings, action_cost=self._spent, month_cost=self._month, next_call=estimate
        )
        if not limit.ok and not self._approved:
            raise _Stop(RunOutcome("needs_approval", limit.reason, round(estimate, 4)))
        text = await self._stream(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], think
        )
        try:
            return parse_json_object(text)
        except ValueError:
            logger.warning("claims answer was not JSON: %.200s", text)
            raise _Unreadable(RunOutcome("failed", _unreadable_message(text))) from None

    async def _stream(self, messages: list[dict[str, Any]], think: bool) -> str:
        if think not in self._models:
            try:
                self._models[think] = self._factory(self.thinking if think else self.settings)
            except AiNotConfiguredError as e:
                raise _Stop(RunOutcome("not_configured", str(e))) from None
        model = self._models[think]
        text = ""
        try:
            async for event in model.stream(messages, []):
                if isinstance(event, TextDelta):
                    text += event.text
                elif isinstance(event, Usage):
                    self._spent += await asyncio.to_thread(
                        record_call, self.settings, event, "claims", self.topic
                    )
        except Exception:
            logger.exception("claims model call failed")
            raise _Stop(
                RunOutcome("failed", "The model call failed. Check the server log, then try again.")
            ) from None
        return text


def _snapshot(run: _Run) -> _Snapshot:
    db_path = run.settings.compass_db_path
    with readonly(db_path) as con:
        notes = eligible_notes(con, run.topic, run.vault.definitions)
        claims = valid_claims(con, notes)
        sig = signature(c.id for c in claims)
        _proposals, proposals_sig, chosen = load_questions(con, run.topic)
        evergreens = offered_evergreens(con, run.topic, run.vault.definitions)
        agreement_sig = agreement_signature(con, claims, evergreens)
        base = load_analysis(con, run.topic, QUESTIONS_BASE_KIND, "")
        return _Snapshot(
            pending=pending_notes(con, notes),
            claims=claims,
            evergreens=evergreens,
            sig=sig,
            agreement_sig=agreement_sig,
            has_questions=questions_current(con, run.topic, proposals_sig, claims),
            needs_questions_base=proposals_sig == sig and (base is None or base[0] != sig),
            chosen=chosen,
            has_agreement=cached_for(con, run.topic, AGREEMENT_KIND, "", agreement_sig) is not None,
            has_stances=chosen is not None
            and cached_for(con, run.topic, STANCES_KIND, chosen, sig) is not None,
            has_summary=chosen is not None
            and cached_for(con, run.topic, SUMMARY_KIND, chosen, sig) is not None,
        )


def _batches(notes: list[tuple[EligibleNote, str]]) -> list[list[tuple[EligibleNote, str]]]:
    batches: list[list[tuple[EligibleNote, str]]] = []
    size = 0
    for item in notes:
        cost = len(item[1])
        if not batches or size + cost > BATCH_CHARS or len(batches[-1]) >= MAX_NOTES_PER_BATCH:
            batches.append([])
            size = 0
        batches[-1].append(item)
        size += cost
    return batches


def _store_claims(run: _Run, results: list[tuple[EligibleNote, list[str], str]]) -> None:
    with writable(run.settings.compass_db_path) as con:
        for note, texts, origin in results:
            save_claims(con, note, [(text, origin) for text in texts])


def _note_block(note: EligibleNote, body: str) -> str:
    return f"### NOTE {note.path} ({note.title})\n{body[:NOTE_CHARS]}"


async def _read_notes(run: _Run, pending: list[EligibleNote]) -> None:
    free: list[tuple[EligibleNote, list[str], str]] = []
    for_model: list[tuple[EligibleNote, str]] = []
    for note in pending[:MAX_NOTES_PER_RUN]:
        body = run.vault.body(note.path)
        own = key_claims(body) if body else []
        if own:
            free.append((note, own, KEY_CLAIMS_ORIGIN))
        elif body and body.strip():
            for_model.append((note, _note_block(note, body)))
        else:
            free.append((note, [], MODEL_ORIGIN))
    await asyncio.to_thread(_store_claims, run, free)
    for batch in _batches(for_model):
        await _extract_batch(run, batch)


async def _extract_batch(run: _Run, batch: list[tuple[EligibleNote, str]]) -> None:
    """Read one batch; if the answer is unusable, read its notes one by one instead."""
    try:
        data = await run.ask(EXTRACT_SYSTEM, "\n\n".join(block for _n, block in batch))
    except _Unreadable:
        if len(batch) == 1:
            raise
        for single in ([item] for item in batch):
            await _extract_batch(run, single)
        return
    found = parse_extracted(data, {n.path for n, _b in batch})
    results = [(n, found.get(n.path, []), MODEL_ORIGIN) for n, _b in batch]
    await asyncio.to_thread(_store_claims, run, results)


def _chunks(claims: list[ClaimItem], size: int = CHUNK_CLAIMS) -> list[list[ClaimItem]]:
    return [claims[i : i + size] for i in range(0, len(claims), size)]


def _numbered(claims: list[ClaimItem], *, titles: bool) -> str:
    return "\n".join(
        f"{n} | {c.title} | {c.text}" if titles else f"{n}. {c.text}"
        for n, c in enumerate(claims, start=1)
    )


def _by_note(claims: list[ClaimItem]) -> str:
    """Claims grouped under their note ("N<i> <title>"), numbered 1.. across the whole list."""
    lines: list[str] = []
    note = 0
    path = None
    for n, c in enumerate(claims, start=1):
        if c.path != path:
            path = c.path
            note += 1
            lines.append(f"N{note} {c.title}")
        lines.append(f"{n}. {c.text}")
    return "\n".join(lines)


def _fit(claims: list[ClaimItem]) -> list[ClaimItem]:
    """All claims, or every k-th one for the smallest k whose prompt fits the size cap."""
    step = 1
    while len(_by_note(claims[::step])) > MAX_QUESTION_PROMPT_CHARS:
        step += 1
    if step > 1:
        logger.warning("questions prompt too long: every %dth of %d claims", step, len(claims))
    return claims[::step]


def _save_proposals(run: _Run, questions: list[dict[str, Any]], claim_ids: list[str]) -> None:
    with writable(run.settings.compass_db_path) as con:
        save_proposals(con, run.topic, questions, claim_ids)


def _save_analysis(run: _Run, kind: str, key: str, sig: str, payload: Any) -> None:
    with writable(run.settings.compass_db_path) as con:
        save_analysis(con, run.topic, kind, key, sig, payload)


def _progress(run: _Run, kind: str, key: str, sig: str) -> Any | None:
    with readonly(run.settings.compass_db_path) as con:
        return cached_for(con, run.topic, kind, key, sig)


def _latest(run: _Run, kind: str, key: str) -> tuple[str, Any] | None:
    """The last saved (signature, payload), whatever claim set it was made from."""
    with readonly(run.settings.compass_db_path) as con:
        return load_analysis(con, run.topic, kind, key)


async def _propose(run: _Run, snap: _Snapshot) -> None:
    """Suggest questions, unless the saved ones still stand for these claims (#175)."""
    ids = [c.id for c in snap.claims]
    if snap.has_questions:
        if snap.needs_questions_base:
            base = sorted(ids)
            await asyncio.to_thread(_save_analysis, run, QUESTIONS_BASE_KIND, "", snap.sig, base)
        return
    claims = _fit(snap.claims)
    data = await run.ask(QUESTIONS_SYSTEM, _by_note(claims), think=True)
    await asyncio.to_thread(_save_proposals, run, parse_questions(data, claims), ids)


def _evergreen_line(run: _Run, evergreen: NoteRef) -> str:
    start = (run.vault.body(evergreen.path) or "")[:EVERGREEN_CHARS]
    return f"{evergreen.title} | {' '.join(start.split())}"


def _fresh_entry(line: str) -> dict[str, Any]:
    """An evergreen no claim has been matched against yet."""
    return {
        "line": line,
        "scanned": [],
        "claim_ids": [],
        "text": "",
        "best": 0,
        "kept": [],
        "shared_text": "",
        "confirmed": True,
    }


def _pruned_entry(entry: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    """An evergreen's state without the claims that are gone; losing a kept claim reconfirms."""
    kept = [i for i in entry["kept"] if i in ids]
    return {
        **entry,
        "scanned": [i for i in entry["scanned"] if i in ids],
        "claim_ids": [i for i in entry["claim_ids"] if i in ids],
        "kept": kept,
        "confirmed": entry["confirmed"] and len(kept) == len(entry["kept"]),
    }


def _legacy_entries(
    con: duckdb.DuckDBPyConnection, topic: str, snap: _Snapshot, lines: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """State from a finished run saved before #175, when it covers exactly these claims.

    That format kept only the confirmed claims, so they stand in for the candidates. Anything
    less than a finished run on the same claims and evergreens is matched again.
    """
    final = load_analysis(con, topic, AGREEMENT_KIND, "")
    progress = load_analysis(con, topic, AGREEMENT_PROGRESS_KIND, "")
    if final is None or progress is None or final[0] != snap.sig or progress[0] != snap.sig:
        return {}
    state = progress[1]
    found: dict[str, Any] = state.get("found", {})
    pairs = len(lines) * len(_chunks(snap.claims))
    if state.get("chunks") != pairs or not set(found) <= set(state.get("confirmed", [])):
        return {}
    if not set(found) <= set(lines):
        return {}
    ids = [c.id for c in snap.claims]
    out = {}
    for path, line in lines.items():
        entry = _fresh_entry(line)
        old = found.get(path)
        if old is not None:
            kept = list(old["claim_ids"])
            entry.update(claim_ids=kept, kept=kept, text=old["text"], shared_text=old["text"])
            entry["best"] = len(kept)
        entry["scanned"] = ids
        out[path] = entry
    return out


def _agreement_start(run: _Run, snap: _Snapshot) -> dict[str, Any]:
    """The saved matching state, fitted to the current claims and evergreens (#175).

    Claims that are gone are dropped; an evergreen that is new or whose text changed starts
    over. Saved after every call, so a stopped run also resumes from here.
    """
    lines = {e.path: _evergreen_line(run, e) for e in snap.evergreens}
    with readonly(run.settings.compass_db_path) as con:
        saved = load_analysis(con, run.topic, AGREEMENT_STATE_KIND, "")
        previous = saved[1]["evergreens"] if saved else _legacy_entries(con, run.topic, snap, lines)
    ids = {c.id for c in snap.claims}
    entries = {}
    for path, line in lines.items():
        old = previous.get(path)
        if old is not None and old["line"] == line:
            entries[path] = _pruned_entry(old, ids)
        else:
            entries[path] = _fresh_entry(line)
    return {"evergreens": entries}


async def _agree(run: _Run, snap: _Snapshot) -> None:
    """Match claims to evergreens, one evergreen and one chunk per call (#173).

    Matching a chunk against all evergreens at once made the model misread claim numbers.
    Only claims an evergreen was not matched against yet are sent (#175), so after a small
    change a run costs a call or two per evergreen, and a stopped run resumes.
    """
    if snap.has_agreement:
        return
    state = await asyncio.to_thread(_agreement_start, run, snap)
    entries: dict[str, dict[str, Any]] = state["evergreens"]
    for evergreen in snap.evergreens:
        entry = entries[evergreen.path]
        scanned = set(entry["scanned"])
        todo = [c for c in snap.claims if c.id not in scanned]
        for chunk in _chunks(todo):
            user = f"EVERGREEN\n{entry['line']}\n\nCLAIMS\n{_numbered(chunk, titles=True)}"
            matches = parse_matches(await run.ask(AGREEMENT_SYSTEM, user), chunk, evergreen)
            if matches:
                merge_matches(entries, matches)
                entry["confirmed"] = False
            entry["scanned"] += [c.id for c in chunk]
            await _save_state(run, snap, state)
    await _confirm(run, snap, state)
    # Also when no call was needed: dropped claims and evergreens are saved with the new sig.
    await _save_state(run, snap, state)
    found = {
        path: {"text": e["shared_text"], "claim_ids": e["kept"]}
        for path, e in entries.items()
        if e["kept"]
    }
    shared = finalize_shared(found, snap.claims)
    await asyncio.to_thread(_save_analysis, run, AGREEMENT_KIND, "", snap.agreement_sig, shared)


async def _save_state(run: _Run, snap: _Snapshot, state: dict[str, Any]) -> None:
    await asyncio.to_thread(
        _save_analysis, run, AGREEMENT_STATE_KIND, "", snap.agreement_sig, state
    )


async def _confirm(run: _Run, snap: _Snapshot, state: dict[str, Any]) -> None:
    """Second pass per evergreen: keep the candidates that make its point, closest first.

    Chunk by chunk the model always finds a few matches, so candidates pile up across chunks.
    Seeing them together, it keeps at most MAX_SHARED_CLAIMS. Only evergreens whose
    candidates changed are confirmed again: new candidates, or a kept claim gone (#175).
    """
    by_id = {c.id: c for c in snap.claims}
    for entry in state["evergreens"].values():
        if entry["confirmed"]:
            continue
        candidates = [by_id[i] for i in entry["claim_ids"] if i in by_id]
        if candidates:
            user = f"EVERGREEN\n{entry['line']}\n\nCLAIMS\n{_numbered(candidates, titles=True)}"
            data = await run.ask(CONFIRM_SYSTEM, user)
            kept = [candidates[n - 1].id for n in _numbers(data.get("c"), len(candidates))]
            entry["kept"] = kept[:MAX_SHARED_CLAIMS]
            entry["shared_text"] = _text(data.get("text")) or entry["text"]
        # With no candidates left, `kept` is already empty: nothing to ask.
        entry["confirmed"] = True
        await _save_state(run, snap, state)


def _carried_stances(run: _Run, question: str, ids: set[str]) -> dict[str, str]:
    """Stances saved for the question from any claim set, for the claims that still exist."""
    done: dict[str, str] = {}
    for kind in (STANCES_KIND, STANCES_PROGRESS_KIND):
        saved = _latest(run, kind, question)
        if saved is not None:
            done.update(saved[1])
    return {cid: stance for cid, stance in done.items() if cid in ids}


async def _sort(run: _Run, snap: _Snapshot) -> None:
    """Sort the claims without a stance yet, chunk by chunk; progress is saved after each."""
    question = snap.chosen
    if question is None or snap.has_stances:
        return
    ids = {c.id for c in snap.claims}
    done = await asyncio.to_thread(_carried_stances, run, question, ids)
    todo = [c for c in snap.claims if c.id not in done]
    for chunk in _chunks(todo, STANCE_CHUNK_CLAIMS):
        data = await run.ask(
            STANCE_SYSTEM, f"QUESTION\n{question}\n\nCLAIMS\n{_numbered(chunk, titles=False)}"
        )
        done.update(parse_stances(data, chunk))
        await asyncio.to_thread(
            _save_analysis, run, STANCES_PROGRESS_KIND, question, snap.sig, done
        )
    await asyncio.to_thread(_save_analysis, run, STANCES_KIND, question, snap.sig, done)


NO_CLAIMS_SUMMARY = "No claims in these notes are about this question."


async def _summarize(run: _Run, snap: _Snapshot) -> None:
    """Summarise the split on the chosen question; one call, redone only when a side changed."""
    question = snap.chosen
    if question is None or snap.has_summary:
        return
    stances = await asyncio.to_thread(_progress, run, STANCES_KIND, question, snap.sig) or {}
    sides = {
        side: [c for c in snap.claims if stances.get(c.id) == side]
        for side in ("supporting", "pushing_back")
    }
    side_ids = {side: sorted(c.id for c in claims) for side, claims in sides.items()}
    previous = await asyncio.to_thread(_latest, run, SUMMARY_KIND, question)
    if previous is not None and previous[1].get("sides") == side_ids:
        await asyncio.to_thread(_save_analysis, run, SUMMARY_KIND, question, snap.sig, previous[1])
        return
    claims = sides["supporting"] + sides["pushing_back"]
    if not claims:
        summary: dict[str, Any] = {
            "bottom_line": NO_CLAIMS_SUMMARY,
            "agree": [],
            "split": [],
            "missing": [],
            "read_ids": [],
        }
    else:
        n = len(sides["supporting"])
        user = (
            f"QUESTION\n{question}\n\nFOR\n{_numbered(sides['supporting'], titles=True)}"
            f"\n\nAGAINST\n"
            + "\n".join(
                f"{n + i} | {c.title} | {c.text}"
                for i, c in enumerate(sides["pushing_back"], start=1)
            )
        )
        parsed = parse_summary(await run.ask(SUMMARY_SYSTEM, user), claims)
        if parsed is None:
            raise _Unreadable(RunOutcome("failed", UNREADABLE))
        summary = parsed
    # The claims on each side, so a later run can tell whether the summary still holds.
    summary["sides"] = side_ids
    await asyncio.to_thread(_save_analysis, run, SUMMARY_KIND, question, snap.sig, summary)


async def run_topic_claims(
    settings: CompassSettings,
    vault: AiVault,
    model_factory: Callable[[CompassSettings], ChatModel],
    topic: str,
    *,
    approved: bool = False,
) -> RunOutcome:
    """Bring the topic's cached claims up to date. Steps that are already current cost nothing."""
    # Short structured answers: no thinking, and room for a long list of letters or numbers.
    tuned = settings.model_copy(
        update={
            "compass_ai_reasoning": False,
            "compass_ai_max_output_tokens": settings.compass_ai_claims_max_output_tokens,
        }
    )
    # The questions step thinks first: without it the questions turn generic (#166).
    thinking = settings.model_copy(
        update={
            "compass_ai_reasoning": True,
            "compass_ai_max_output_tokens": settings.compass_ai_questions_max_output_tokens,
        }
    )
    run = _Run(tuned, vault, model_factory, topic, approved, thinking)
    try:
        snap = await asyncio.to_thread(_snapshot, run)
        if snap.pending:
            await _read_notes(run, snap.pending)
            snap = await asyncio.to_thread(_snapshot, run)
        # Analyse only once every note is read, so a partial set is not paid for twice.
        if snap.claims and not snap.pending:
            await _propose(run, snap)
            await _agree(run, snap)
            await _sort(run, snap)
            await _summarize(run, snap)
    except _Stop as stop:
        return stop.outcome
    except Exception as exc:
        # A bug or odd data must not become an opaque 500: say what happened, keep the log.
        logger.exception("claims run crashed")
        message = f"Unexpected error ({type(exc).__name__}: {str(exc)[:150]}). See the server log."
        return RunOutcome("failed", message)
    return RunOutcome("done")
