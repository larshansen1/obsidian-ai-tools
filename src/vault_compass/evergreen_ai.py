"""Write and revise an evergreen draft with the model (T8 follow-up).

The draft lives in the browser. Each call takes the current title and text plus
the user's instruction and returns a new version; nothing is written to the vault
here. Saving still goes through the write layer (evergreen_draft.plan_evergreen).

The "Claims this draws on" section is kept by code, not by the model: the model
sees and returns only the prose, and the section from the current draft is put
back under it, so its links to the source notes survive every revision.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from .ai_client import AiNotConfiguredError, ChatModel, TextDelta, Usage
from .ai_cost import check_limits, estimate_cost, month_spend, record_call
from .claims import SplitSummary
from .claims_run import RunStatus
from .config import CompassSettings
from .evergreen_draft import SOURCES_HEADING

logger = logging.getLogger(__name__)

USAGE_NAME = "evergreen_draft"

WRITE_SYSTEM = """\
You write evergreen notes for a personal Obsidian vault. An evergreen note states one idea \
as a claim, in the owner's own voice, so it stays useful for years.

Write the note from the claims you are given. Rules:
- The title is the idea itself, stated as a claim in one sentence, not a topic label.
- Open with two or three sentences that state the idea plainly.
- Then explain why it holds and where it stops holding. Use short sections with ## headings \
when the note needs more than a few paragraphs.
- Use only what the claims say. Do not invent studies, numbers or quotes.
- No [[links]] and no list of sources: the sources are added below the note for you.

Answer with the note only, in Markdown. The first line is the title as "# Title". \
Nothing before it, nothing after the note."""

ANSWER_SYSTEM = """\
You write evergreen notes for a personal Obsidian vault. An evergreen note states one idea \
as a claim, in the owner's own voice, so it stays useful for years.

The owner asked a question of their notes. You get the question, a summary of how the notes \
answer it, and claims from both sides. Write the note that answers the question. Rules:
- The title is the answer itself, stated as a claim in one sentence. Say under which \
conditions it holds when the notes split; do not sit on the fence.
- Open with two or three sentences that state the answer plainly.
- Then give the strongest case for it, the strongest case against it, and what would settle \
it. Use short sections with ## headings.
- Use only what the summary and the claims say. Do not invent studies, numbers or quotes.
- No [[links]] and no list of sources: the sources are added below the note for you.

Answer with the note only, in Markdown. The first line is the title as "# Title". \
Nothing before it, nothing after the note."""

REVISE_SYSTEM = """\
You revise an evergreen note in a personal Obsidian vault. Apply the owner's instruction \
to the current note and change nothing else they did not ask for.

Rules:
- Use only what the note and the claims say. Do not invent studies, numbers or quotes.
- No [[links]] and no list of sources: the sources are kept below the note for you.

Answer with the whole revised note in Markdown. The first line is the title as "# Title". \
Nothing before it, nothing after the note."""

FAILED = "The model call failed. Check the server log, then try again."
EMPTY = "The model sent an empty answer. Try again, or pick another model with LLM_MODEL."

_TITLE = re.compile(r"#\s+(.+?)\s*#*\s*")
_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class DraftOutcome:
    status: RunStatus
    message: str | None
    estimate_usd: float | None
    title: str
    body: str


def split_sources(body: str) -> tuple[str, str]:
    """The draft's prose, and its "Claims this draws on" section ("" when there is none)."""
    text = body.replace("\r\n", "\n")
    at = text.find(SOURCES_HEADING)
    if at == -1:
        return text.strip("\n"), ""
    return text[:at].strip("\n"), text[at:].strip("\n")


def join_sources(prose: str, sources: str) -> str:
    """Prose, then the sources section below it, ending in one newline."""
    parts = [p for p in (prose.strip("\n"), sources.strip("\n")) if p]
    return "\n\n".join(parts) + "\n" if parts else ""


def parse_note(text: str) -> tuple[str, str] | None:
    """(title, prose) from a model answer, or None when it has no text.

    A code fence around the note is removed. Without a "# Title" first line the
    title is "" so the caller keeps the one it had. A sources section the model
    added anyway is dropped: the caller puts the real one back.
    """
    fenced = _FENCE.fullmatch(text.strip())
    lines = (fenced.group(1) if fenced else text).strip().splitlines()
    if not lines:
        return None
    heading = _TITLE.fullmatch(lines[0].strip())
    title = heading.group(1).strip() if heading else ""
    prose, _ = split_sources("\n".join(lines[1:] if heading else lines))
    if not title and not prose:
        return None
    return title, prose


def _claims_block(claims: list[str]) -> str:
    return "\n".join(f"{i}. {text}" for i, text in enumerate(claims, start=1))


def _section(title: str, items: list[str]) -> list[str]:
    return [f"{title}:", *(f"- {item}" for item in items)] if items else []


def answer_block(question: str, summary: SplitSummary) -> str:
    """The chosen question and how the notes answer it, for a draft made from the answer."""
    lines = [f"Question: {question}", "", f"Bottom line: {summary.bottom_line}"]
    for title, items in (
        ("Where they agree", summary.agree),
        ("Where they split", summary.split),
        ("What is missing", summary.missing),
    ):
        if items:
            lines += ["", *_section(title, items)]
    return "\n".join(lines)


def draft_prompt(
    topic_name: str,
    claims: list[str],
    title: str,
    prose: str,
    instruction: str | None,
    answer: str | None = None,
) -> tuple[str, str]:
    """(system, user) for one call: a first draft when there is no instruction, else a revision.

    `answer` (from `answer_block`) makes the draft answer the chosen question; it stays in the
    prompt for revisions, so the model keeps the whole picture.
    """
    context = f"\n\n{answer}" if answer else ""
    head = f"Topic: {topic_name}{context}\n\nClaims:\n{_claims_block(claims)}"
    if instruction is None:
        return (ANSWER_SYSTEM if answer else WRITE_SYSTEM), head
    current = f"# {title}\n\n{prose}" if title else prose
    user = f"{head}\n\nCurrent note:\n{current}\n\nInstruction: {instruction}"
    return REVISE_SYSTEM, user


def _tuned(settings: CompassSettings) -> CompassSettings:
    # A whole note needs more room than a chat reply; thinking would spend it before writing.
    return settings.model_copy(
        update={
            "compass_ai_reasoning": False,
            "compass_ai_max_output_tokens": settings.compass_ai_draft_max_output_tokens,
        }
    )


async def _ask(
    settings: CompassSettings,
    model: ChatModel,
    system: str,
    user: str,
    topic: str,
) -> str:
    text = ""
    async for event in model.stream(
        [{"role": "system", "content": system}, {"role": "user", "content": user}], []
    ):
        if isinstance(event, TextDelta):
            text += event.text
        elif isinstance(event, Usage):
            await asyncio.to_thread(record_call, settings, event, USAGE_NAME, topic)
    return text


async def write_draft(
    settings: CompassSettings,
    model_factory: Callable[[CompassSettings], ChatModel],
    *,
    topic: str,
    topic_name: str,
    claims: list[str],
    title: str,
    body: str,
    instruction: str | None,
    answer: str | None = None,
    approved: bool = False,
) -> DraftOutcome:
    """A first draft (no instruction) or a revision of `title` and `body`.

    On anything but "done" the draft comes back unchanged, so the browser never loses it.
    """
    tuned = _tuned(settings)
    prose, sources = split_sources(body)

    def unchanged(status: RunStatus, message: str, estimate: float | None = None) -> DraftOutcome:
        return DraftOutcome(status, message, estimate, title, body)

    system, user = draft_prompt(topic_name, claims, title.strip(), prose, instruction, answer)
    estimate = estimate_cost(tuned, len(system) + len(user))
    month = await asyncio.to_thread(month_spend, settings.compass_db_path)
    limit = check_limits(tuned, action_cost=0.0, month_cost=month, next_call=estimate)
    if not limit.ok and not approved:
        return unchanged("needs_approval", limit.reason or "", round(estimate, 4))
    try:
        model = model_factory(tuned)
    except AiNotConfiguredError as e:
        return unchanged("not_configured", str(e))
    try:
        answer = await _ask(tuned, model, system, user, topic)
    except Exception:
        logger.exception("evergreen draft model call failed")
        return unchanged("failed", FAILED)
    note = parse_note(answer)
    if note is None:
        logger.warning("evergreen draft answer was empty: %.200s", answer)
        return unchanged("failed", EMPTY)
    new_title, new_prose = note
    return DraftOutcome("done", None, None, new_title or title, join_sources(new_prose, sources))
