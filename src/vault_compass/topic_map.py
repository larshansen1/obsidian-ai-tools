"""Numbers for the topic map screen: summary tiles and per-topic stats (M1-M5, M7).

Pure reads from compass.duckdb. "Worked through" is the evergreen count
(ADR 0004); links are a separate number. Dates are counted by the note's
created date, and `today` is passed in so the numbers are reproducible.
"""

from collections import Counter
from datetime import date, timedelta
from typing import Literal

import duckdb
from pydantic import BaseModel

from .signals import Signal, find_signals
from .topic_notes import TopicNote, load_topic_notes, source_name
from .topics import TopicsFile

Window = Literal["30", "90", "all"]

DAYS_PER_MONTH = 30
BASELINE_DAYS = 90
FAST_GROWTH_PERCENT = 50.0

# window -> (days in the recent period, days of baseline before it; None = back to trend_start)
_WINDOWS: dict[str, tuple[int, int | None]] = {
    "30": (30, BASELINE_DAYS),
    "90": (90, BASELINE_DAYS),
    "all": (30, None),
}

MOMENTUM_FORMULA = (
    "Momentum is the percent change between two monthly rates. Recent rate: notes created "
    "in the window (last 30 days, last 90 days, or, for all time, the last 30 days) per "
    "30 days. Baseline rate: notes created in the 90 days before the window (for all time, "
    "everything before it) per 30 days. Notes before the trend start date are ignored. "
    "No baseline notes means no momentum."
)

_TILES_SQL = """
SELECT
    COUNT(*),
    COUNT(*) FILTER (WHERE created > ? AND created <= ?),
    COUNT(*) FILTER (WHERE created > ? AND created <= ?),
    COUNT(*) FILTER (WHERE is_evergreen),
    COUNT(*) FILTER (WHERE folder = ? OR starts_with(folder, ? || '/'))
FROM notes
"""

_LINKED_NOTES_SQL = "SELECT COUNT(DISTINCT source_path) FROM links"


class Tiles(BaseModel):
    total_notes: int
    notes_last_30: int
    notes_prior_30: int
    # Fraction 0..1 of all notes with at least one outgoing link.
    link_share: float
    evergreen_count: int
    inbox_count: int


class MonthCount(BaseModel):
    month: str
    notes: int


class TopSource(BaseModel):
    name: str
    notes: int


class TopicStats(BaseModel):
    id: str
    name: str
    note_count: int
    # Notes created in the selected window; all time counts every note.
    window_notes: int
    # Percent change (M3); None when there is no baseline to compare with.
    momentum: float | None
    evergreens: int
    linked_notes: int
    notes_per_month: list[MonthCount]
    top_source: TopSource | None
    below_min_notes: bool
    next_step: str


class TopicMapResponse(BaseModel):
    window: Window
    min_notes: int
    momentum_formula: str
    tiles: Tiles
    topics: list[TopicStats]
    signals: list[Signal]


def _count_between(notes: list[TopicNote], after: date, upto: date) -> int:
    """Notes created in (after, upto]."""
    return sum(1 for n in notes if n.created is not None and after < n.created <= upto)


def _momentum(
    notes: list[TopicNote], today: date, window: Window, trend_start: date
) -> float | None:
    recent_days, baseline_days = _WINDOWS[window]
    recent_start = today - timedelta(days=recent_days)
    baseline_floor = trend_start - timedelta(days=1)
    baseline_after = baseline_floor
    if baseline_days is not None:
        baseline_after = max(recent_start - timedelta(days=baseline_days), baseline_floor)
    span = (recent_start - baseline_after).days
    baseline_count = _count_between(notes, baseline_after, recent_start)
    if span <= 0 or baseline_count == 0:
        return None
    baseline_rate = baseline_count / (span / DAYS_PER_MONTH)
    recent_rate = _count_between(notes, recent_start, today) / (recent_days / DAYS_PER_MONTH)
    return round((recent_rate / baseline_rate - 1) * 100, 1)


def _window_notes(notes: list[TopicNote], today: date, window: Window) -> int:
    if window == "all":
        return len(notes)
    return _count_between(notes, today - timedelta(days=_WINDOWS[window][0]), today)


def _notes_per_month(notes: list[TopicNote], today: date, trend_start: date) -> list[MonthCount]:
    counts = Counter(
        (n.created.year, n.created.month)
        for n in notes
        if n.created is not None and trend_start <= n.created <= today
    )
    months = []
    year, month = trend_start.year, trend_start.month
    while (year, month) <= (today.year, today.month):
        months.append(MonthCount(month=f"{year:04d}-{month:02d}", notes=counts[(year, month)]))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _top_source(notes: list[TopicNote]) -> TopSource | None:
    counts = Counter(name for n in notes if (name := source_name(n)) is not None)
    if not counts:
        return None
    # Most common first; ties go to the name that sorts first.
    name, count = min(counts.items(), key=lambda item: (-item[1], item[0]))
    return TopSource(name=name, notes=count)


def _next_step(*, below_min: bool, evergreens: int, momentum: float | None, unlinked: int) -> str:
    if below_min:
        return "Too few notes to read a trend. Keep collecting."
    if evergreens == 0:
        return "Write your first evergreen note on this topic."
    if momentum is not None and momentum >= FAST_GROWTH_PERCENT:
        return "Growing fast. Turn the recent notes into an evergreen."
    if unlinked > 0:
        return "Link the notes that have no links yet."
    return "Revisit your evergreens and look for gaps."


def topic_stats(
    topic_id: str,
    name: str,
    notes: list[TopicNote],
    *,
    definitions: TopicsFile,
    today: date,
    window: Window,
) -> TopicStats:
    count = len(notes)
    evergreens = sum(1 for n in notes if n.is_evergreen)
    linked = sum(1 for n in notes if n.has_link)
    momentum = _momentum(notes, today, window, definitions.trend_start)
    below_min = count < definitions.min_notes
    return TopicStats(
        id=topic_id,
        name=name,
        note_count=count,
        window_notes=_window_notes(notes, today, window),
        momentum=momentum,
        evergreens=evergreens,
        linked_notes=linked,
        notes_per_month=_notes_per_month(notes, today, definitions.trend_start),
        top_source=_top_source(notes),
        below_min_notes=below_min,
        next_step=_next_step(
            below_min=below_min,
            evergreens=evergreens,
            momentum=momentum,
            unlinked=count - linked,
        ),
    )


def _tiles(con: duckdb.DuckDBPyConnection, today: date, inbox_folder: str) -> Tiles:
    last_start = today - timedelta(days=30)
    prior_start = today - timedelta(days=60)
    row = con.execute(
        _TILES_SQL, [last_start, today, prior_start, last_start, inbox_folder, inbox_folder]
    ).fetchone()
    assert row is not None  # an aggregate query always returns one row
    total, last_30, prior_30, evergreens, inbox = (int(v) for v in row)
    linked_row = con.execute(_LINKED_NOTES_SQL).fetchone()
    assert linked_row is not None
    return Tiles(
        total_notes=total,
        notes_last_30=last_30,
        notes_prior_30=prior_30,
        link_share=int(linked_row[0]) / total if total else 0.0,
        evergreen_count=evergreens,
        inbox_count=inbox,
    )


def build_topic_map(
    con: duckdb.DuckDBPyConnection,
    definitions: TopicsFile,
    *,
    today: date,
    window: Window,
    inbox_folder: str,
) -> TopicMapResponse:
    by_topic = load_topic_notes(con)
    topics = [
        topic_stats(
            topic_id,
            topic.name,
            by_topic.get(topic_id, []),
            definitions=definitions,
            today=today,
            window=window,
        )
        for topic_id, topic in definitions.topics.items()
    ]
    return TopicMapResponse(
        window=window,
        min_notes=definitions.min_notes,
        momentum_formula=MOMENTUM_FORMULA,
        tiles=_tiles(con, today, inbox_folder),
        topics=topics,
        signals=find_signals(by_topic, definitions, today),
    )
