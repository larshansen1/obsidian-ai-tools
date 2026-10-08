"""The model steps behind the claims screen (T3, T4, Q6, Q7).

One run reads the topic's unread notes, then asks for question proposals, shared
claims and stances, skipping every step whose cached result is still current.
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

from .ai_client import AiNotConfiguredError, ChatModel, TextDelta, Usage
from .ai_cost import check_limits, estimate_cost, month_spend, record_call
from .claims import (
    AGREEMENT_KIND,
    STANCES,
    STANCES_KIND,
    ClaimItem,
    EligibleNote,
    cached_for,
    eligible_notes,
    evergreen_notes,
    load_questions,
    pending_notes,
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
MAX_PROMPT_CLAIMS = 150
EVERGREEN_CHARS = 400
MAX_QUESTIONS = 3
KEY_CLAIMS_ORIGIN = "key_claims"
MODEL_ORIGIN = "model"

RunStatus = Literal["done", "needs_approval", "not_configured", "failed"]

EXTRACT_SYSTEM = f"""\
You read notes and list the claims each one makes. A claim is one self-contained statement \
the source asserts: a finding, a recommendation or a position. Give 1 to {MAX_CLAIMS_PER_NOTE} \
claims per note, each under 160 characters, in your own words. Reply with JSON only: \
{{"claims": [{{"note": "<note path exactly as given>", "text": "<claim>"}}]}}"""

QUESTIONS_SYSTEM = f"""\
You are given claims from notes on one topic. Propose 2 to {MAX_QUESTIONS} questions the \
claims bear on, where some claims could support an answer and others push back. Each question \
must be short and open to disagreement. Reply with JSON only: {{"questions": ["..."]}}"""

AGREEMENT_SYSTEM = """\
You are given claims from notes (id, note, text) and a list of the user's evergreen notes \
(path, title, start). Find shared claims: statements made by at least 2 different notes. \
Word each in one line, list the ids that make it, and name the evergreen path it matches, or \
null when none fits. Use only ids and paths you were given. Reply with JSON only: \
{"shared": [{"text": "...", "claim_ids": ["..."], "evergreen": "<path or null>"}]}"""

STANCE_SYSTEM = """\
You are given a question and claims (id, text). For each claim say whether it is \
"supporting" an answer of yes to the question, "pushing_back" against it, or "unrelated". \
Reply with JSON only: {"stances": {"<id>": "supporting|pushing_back|unrelated"}}"""

_KEY_CLAIMS_HEADING = re.compile(r"^#{1,6}\s*key claims\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_BULLET = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+(.*\S)")


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    message: str | None = None
    estimate_usd: float | None = None


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


def _text(value: Any) -> str:
    return value.strip()[:MAX_CLAIM_CHARS] if isinstance(value, str) else ""


def parse_extracted(data: dict[str, Any], paths: set[str]) -> dict[str, list[str]]:
    """Claims per note path. Claims naming a note outside the batch are dropped."""
    out: dict[str, list[str]] = {}
    for item in data.get("claims") or []:
        if not isinstance(item, dict):
            continue
        note, text = item.get("note"), _text(item.get("text"))
        if note not in paths or not text:
            continue
        bucket = out.setdefault(note, [])
        if len(bucket) < MAX_CLAIMS_PER_NOTE:
            bucket.append(text)
    return out


def parse_questions(data: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    for value in data.get("questions") or []:
        text = _text(value)
        if text and text not in questions:
            questions.append(text)
    return questions[:MAX_QUESTIONS]


def parse_shared(
    data: dict[str, Any], claims: list[ClaimItem], evergreens: set[str]
) -> list[dict[str, Any]]:
    """Shared claims backed by 2 or more different notes; unknown ids and paths dropped."""
    path_of = {c.id: c.path for c in claims}
    out = []
    for item in data.get("shared") or []:
        if not isinstance(item, dict):
            continue
        ids = [i for i in item.get("claim_ids") or [] if isinstance(i, str) and i in path_of]
        text = _text(item.get("text"))
        if not text or len({path_of[i] for i in ids}) < 2:
            continue
        evergreen = item.get("evergreen")
        out.append(
            {
                "text": text,
                "claim_ids": list(dict.fromkeys(ids)),
                "evergreen": evergreen if evergreen in evergreens else None,
            }
        )
    return out


def parse_stances(data: dict[str, Any], claims: list[ClaimItem]) -> dict[str, str]:
    raw = data.get("stances")
    given = raw if isinstance(raw, dict) else {}
    return {c.id: given[c.id] if given.get(c.id) in STANCES else "unrelated" for c in claims}


@dataclass(frozen=True)
class _Snapshot:
    pending: list[EligibleNote]
    claims: list[ClaimItem]
    evergreens: list[NoteRef]
    sig: str
    proposals_sig: str
    chosen: str | None
    has_agreement: bool
    has_stances: bool


class _Run:
    """One claims run: asks the model, enforcing the cost limits before every call."""

    def __init__(
        self,
        settings: CompassSettings,
        vault: AiVault,
        model_factory: Callable[[CompassSettings], ChatModel],
        topic: str,
        approved: bool,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.topic = topic
        self._factory = model_factory
        self._approved = approved
        self._model: ChatModel | None = None
        self._month: float | None = None
        self._spent = 0.0

    async def ask(self, system: str, user: str) -> dict[str, Any]:
        estimate = estimate_cost(self.settings, len(system) + len(user))
        if self._month is None:
            self._month = await asyncio.to_thread(month_spend, self.settings.compass_db_path)
        limit = check_limits(
            self.settings, action_cost=self._spent, month_cost=self._month, next_call=estimate
        )
        if not limit.ok and not self._approved:
            raise _Stop(RunOutcome("needs_approval", limit.reason, round(estimate, 4)))
        text = await self._stream(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        try:
            return parse_json_object(text)
        except ValueError:
            logger.warning("claims answer was not JSON: %.200s", text)
            raise _Unreadable(
                RunOutcome("failed", "The model's answer could not be read. Try again.")
            ) from None

    async def _stream(self, messages: list[dict[str, Any]]) -> str:
        if self._model is None:
            try:
                self._model = self._factory(self.settings)
            except AiNotConfiguredError as e:
                raise _Stop(RunOutcome("not_configured", str(e))) from None
        model = self._model
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
        return _Snapshot(
            pending=pending_notes(con, notes),
            claims=claims,
            evergreens=evergreen_notes(con, run.topic, run.vault.definitions),
            sig=sig,
            proposals_sig=proposals_sig,
            chosen=chosen,
            has_agreement=cached_for(con, run.topic, AGREEMENT_KIND, "", sig) is not None,
            has_stances=chosen is not None
            and cached_for(con, run.topic, STANCES_KIND, chosen, sig) is not None,
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


def _claim_lines(claims: list[ClaimItem]) -> str:
    return "\n".join(f"{c.id} | {c.title} | {c.text}" for c in claims[:MAX_PROMPT_CLAIMS])


def _save_proposals(run: _Run, questions: list[str], sig: str) -> None:
    with writable(run.settings.compass_db_path) as con:
        save_proposals(con, run.topic, questions, sig)


def _save_analysis(run: _Run, kind: str, key: str, sig: str, payload: Any) -> None:
    with writable(run.settings.compass_db_path) as con:
        save_analysis(con, run.topic, kind, key, sig, payload)


async def _propose(run: _Run, snap: _Snapshot) -> None:
    if snap.proposals_sig == snap.sig:
        return
    data = await run.ask(QUESTIONS_SYSTEM, _claim_lines(snap.claims))
    await asyncio.to_thread(_save_proposals, run, parse_questions(data), snap.sig)


def _evergreen_block(run: _Run, evergreen: NoteRef) -> str:
    body = run.vault.body(evergreen.path)
    return f"{evergreen.path} | {evergreen.title} | {(body or '')[:EVERGREEN_CHARS]!r}"


async def _agree(run: _Run, snap: _Snapshot) -> None:
    if snap.has_agreement:
        return
    evergreens = "\n".join(_evergreen_block(run, e) for e in snap.evergreens)
    user = f"CLAIMS\n{_claim_lines(snap.claims)}\n\nEVERGREENS\n{evergreens or '(none)'}"
    data = await run.ask(AGREEMENT_SYSTEM, user)
    shared = parse_shared(data, snap.claims, {e.path for e in snap.evergreens})
    await asyncio.to_thread(_save_analysis, run, AGREEMENT_KIND, "", snap.sig, shared)


async def _sort(run: _Run, snap: _Snapshot) -> None:
    if snap.chosen is None or snap.has_stances:
        return
    lines = "\n".join(f"{c.id} | {c.text}" for c in snap.claims[:MAX_PROMPT_CLAIMS])
    data = await run.ask(STANCE_SYSTEM, f"QUESTION\n{snap.chosen}\n\nCLAIMS\n{lines}")
    stances = parse_stances(data, snap.claims[:MAX_PROMPT_CLAIMS])
    await asyncio.to_thread(_save_analysis, run, STANCES_KIND, snap.chosen, snap.sig, stances)


async def run_topic_claims(
    settings: CompassSettings,
    vault: AiVault,
    model_factory: Callable[[CompassSettings], ChatModel],
    topic: str,
    *,
    approved: bool = False,
) -> RunOutcome:
    """Bring the topic's cached claims up to date. Steps that are already current cost nothing."""
    run = _Run(settings, vault, model_factory, topic, approved)
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
    except _Stop as stop:
        return stop.outcome
    return RunOutcome("done")
