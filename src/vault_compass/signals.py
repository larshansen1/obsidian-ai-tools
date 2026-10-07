"""Rule-based alerts for the topic map (M6).

Each rule looks at one topic and, when it fires, names the notes behind it.
Topics below the minimum note count never raise a signal (M4). `today` is
passed in so the result is reproducible.
"""

from collections import Counter
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from .topic_notes import TopicNote, source_name
from .topics import TopicsFile

SignalKind = Literal["new_and_growing", "writing_more_than_reading", "source_concentration"]

MAX_SIGNALS = 3
RECENT_DAYS = 30
NEW_TOPIC_DAYS = 90
CONCENTRATION_PERCENT = 50

# Shown first to last when more than MAX_SIGNALS fire.
_KIND_ORDER: tuple[SignalKind, ...] = (
    "new_and_growing",
    "writing_more_than_reading",
    "source_concentration",
)

SIGNAL_RULES = (
    "New and growing fast: the topic's first note is under 90 days old and it has at least the "
    "minimum number of notes in the last 30 days. "
    "Writing more than reading: in the last 30 days you wrote more evergreens than you "
    "saved notes from a source. "
    "Source concentration: at least half of the notes with a known source come from one source."
)


class Signal(BaseModel):
    topic_id: str
    topic_name: str
    kind: SignalKind
    message: str
    # How many notes sit behind the signal; the topic page lists them.
    note_count: int


def _recent(notes: list[TopicNote], today: date) -> list[TopicNote]:
    start = today - timedelta(days=RECENT_DAYS)
    return [n for n in notes if n.created is not None and start < n.created <= today]


def _new_and_growing(notes: list[TopicNote], today: date, min_notes: int) -> list[TopicNote]:
    dated = [n.created for n in notes if n.created is not None]
    if not dated or min(dated) <= today - timedelta(days=NEW_TOPIC_DAYS):
        return []
    recent = _recent(notes, today)
    return recent if len(recent) >= min_notes else []


def _writing_more_than_reading(notes: list[TopicNote], today: date) -> list[TopicNote]:
    recent = _recent(notes, today)
    written = [n for n in recent if n.is_evergreen]
    read = [n for n in recent if not n.is_evergreen and source_name(n) is not None]
    return written if len(written) > len(read) else []


def _source_concentration(notes: list[TopicNote], min_notes: int) -> list[TopicNote]:
    sourced = [n for n in notes if source_name(n) is not None]
    if len(sourced) < min_notes:
        return []
    counts = Counter(source_name(n) for n in sourced)
    # Most common first; ties go to the name that sorts first.
    top, top_count = min(counts.items(), key=lambda item: (-item[1], str(item[0])))
    if top_count * 100 < CONCENTRATION_PERCENT * len(sourced):
        return []
    return [n for n in sourced if source_name(n) == top]


def topic_signal_notes(
    kind: SignalKind, notes: list[TopicNote], definitions: TopicsFile, today: date
) -> list[TopicNote]:
    """The notes behind one rule for one topic; empty when the rule does not fire."""
    if len(notes) < definitions.min_notes:
        return []
    if kind == "new_and_growing":
        return _new_and_growing(notes, today, definitions.min_notes)
    if kind == "writing_more_than_reading":
        return _writing_more_than_reading(notes, today)
    return _source_concentration(notes, definitions.min_notes)


def _message(kind: SignalKind, count: int) -> str:
    if kind == "new_and_growing":
        return f"New and growing fast: {count} notes in the last 30 days."
    if kind == "writing_more_than_reading":
        return f"Writing more than reading: {count} evergreens in the last 30 days."
    return f"Source concentration: {count} notes from one source."


def find_signals(
    by_topic: dict[str, list[TopicNote]], definitions: TopicsFile, today: date
) -> list[Signal]:
    """Up to MAX_SIGNALS alerts: by rule order, then bigger topics first, then topic id."""
    found: list[tuple[int, int, str, Signal]] = []
    for topic_id, topic in definitions.topics.items():
        notes = by_topic.get(topic_id, [])
        for rank, kind in enumerate(_KIND_ORDER):
            behind = topic_signal_notes(kind, notes, definitions, today)
            if behind:
                signal = Signal(
                    topic_id=topic_id,
                    topic_name=topic.name,
                    kind=kind,
                    message=_message(kind, len(behind)),
                    note_count=len(behind),
                )
                found.append((rank, -len(notes), topic_id, signal))
    found.sort(key=lambda item: item[:3])
    return [item[3] for item in found[:MAX_SIGNALS]]
