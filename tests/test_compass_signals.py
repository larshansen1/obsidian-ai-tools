"""Rules for the topic map signals (M6), with the values at their exact limits."""

from datetime import date

import pytest

from vault_compass.signals import (
    MAX_SIGNALS,
    Signal,
    find_signals,
    topic_signal_notes,
)
from vault_compass.topic_notes import TopicNote
from vault_compass.topics import TopicsFile

TODAY = date(2026, 10, 6)


def _defs(*topic_ids: str, min_notes: int = 3) -> TopicsFile:
    return TopicsFile.model_validate(
        {
            "topics": {t: {"name": t.upper(), "tags": [t]} for t in topic_ids},
            "min_notes": min_notes,
            "trend_start": "2026-04-01",
        }
    )


def _n(
    path: str,
    created: date | None = TODAY,
    *,
    evergreen: bool = False,
    url: str | None = None,
    author: str | None = None,
) -> TopicNote:
    return TopicNote(path, path, created, None, evergreen, url, author, False)


def _paths(notes: list[TopicNote]) -> list[str]:
    return [n.path for n in notes]


def test_new_and_growing_fires_at_min_notes_in_30_days() -> None:
    notes = [_n("a", date(2026, 9, 7)), _n("b", date(2026, 9, 8)), _n("c", TODAY)]

    assert _paths(topic_signal_notes("new_and_growing", notes, _defs("t"), TODAY)) == [
        "a",
        "b",
        "c",
    ]


def test_new_and_growing_needs_min_notes_recent() -> None:
    notes = [_n("a", date(2026, 9, 6)), _n("b", date(2026, 9, 8)), _n("c", TODAY)]

    # 2026-09-06 is exactly 30 days back: outside the window.
    assert topic_signal_notes("new_and_growing", notes, _defs("t"), TODAY) == []


def test_new_and_growing_first_note_exactly_90_days_old_is_not_new() -> None:
    old = date(2026, 7, 8)
    notes = [_n("old", old), _n("b", date(2026, 9, 8)), _n("c", date(2026, 9, 9)), _n("d", TODAY)]

    assert topic_signal_notes("new_and_growing", notes, _defs("t"), TODAY) == []


def test_new_and_growing_first_note_89_days_old_is_new() -> None:
    first = date(2026, 7, 9)
    notes = [_n("old", first), _n("b", date(2026, 9, 8)), _n("c", date(2026, 9, 9)), _n("d", TODAY)]

    assert _paths(topic_signal_notes("new_and_growing", notes, _defs("t"), TODAY)) == [
        "b",
        "c",
        "d",
    ]


def test_new_and_growing_ignores_undated_notes() -> None:
    notes = [_n("x", None), _n("b", date(2026, 9, 8)), _n("c", date(2026, 9, 9)), _n("d", TODAY)]

    assert _paths(topic_signal_notes("new_and_growing", notes, _defs("t"), TODAY)) == [
        "b",
        "c",
        "d",
    ]


def test_writing_more_than_reading_needs_strictly_more_evergreens() -> None:
    defs = _defs("t")
    tie = [
        _n("e", evergreen=True),
        _n("r", url="https://a.com/1"),
        _n("p1"),
    ]
    more = [*tie[:1], _n("e2", evergreen=True), tie[1], tie[2]]

    assert topic_signal_notes("writing_more_than_reading", tie, defs, TODAY) == []
    assert _paths(topic_signal_notes("writing_more_than_reading", more, defs, TODAY)) == ["e", "e2"]


def test_writing_more_than_reading_counts_only_the_last_30_days() -> None:
    notes = [
        _n("e", date(2026, 9, 6), evergreen=True),
        _n("e2", date(2026, 9, 7), evergreen=True),
        _n("e3", date(2026, 9, 8), evergreen=True),
    ]

    assert _paths(topic_signal_notes("writing_more_than_reading", notes, _defs("t"), TODAY)) == [
        "e2",
        "e3",
    ]


def test_writing_more_than_reading_ignores_reading_without_a_source() -> None:
    notes = [_n("e", evergreen=True), _n("r1"), _n("r2"), _n("r3")]

    assert _paths(topic_signal_notes("writing_more_than_reading", notes, _defs("t"), TODAY)) == [
        "e"
    ]


def test_source_concentration_at_exactly_half_fires() -> None:
    notes = [
        _n("a1", url="https://a.com/1"),
        _n("a2", url="https://a.com/2"),
        _n("b1", url="https://b.com/1"),
        _n("c1", author="C"),
    ]

    assert _paths(topic_signal_notes("source_concentration", notes, _defs("t"), TODAY)) == [
        "a1",
        "a2",
    ]


def test_source_concentration_below_half_does_not_fire() -> None:
    notes = [
        _n("a1", url="https://a.com/1"),
        _n("a2", url="https://a.com/2"),
        _n("b1", url="https://b.com/1"),
        _n("c1", author="C"),
        _n("d1", author="D"),
    ]

    assert topic_signal_notes("source_concentration", notes, _defs("t"), TODAY) == []


def test_source_concentration_needs_min_sourced_notes() -> None:
    notes = [_n("a1", url="https://a.com/1"), _n("a2", url="https://a.com/2"), _n("u")]

    # Two sourced notes with min_notes 3: too few to call it concentrated.
    assert topic_signal_notes("source_concentration", notes, _defs("t"), TODAY) == []
    assert _paths(
        topic_signal_notes("source_concentration", notes, _defs("t", min_notes=2), TODAY)
    ) == [
        "a1",
        "a2",
    ]


def test_source_concentration_tie_goes_to_first_name() -> None:
    notes = [
        _n("b1", url="https://b.com/1"),
        _n("a1", url="https://a.com/1"),
        _n("b2", url="https://b.com/2"),
        _n("a2", url="https://a.com/2"),
    ]

    assert _paths(topic_signal_notes("source_concentration", notes, _defs("t"), TODAY)) == [
        "a1",
        "a2",
    ]


def test_topic_below_min_notes_never_signals() -> None:
    notes = [_n("e", evergreen=True), _n("e2", evergreen=True)]

    assert topic_signal_notes("writing_more_than_reading", notes, _defs("t"), TODAY) == []


@pytest.fixture
def busy() -> dict[str, list[TopicNote]]:
    evergreens = [_n(f"{t}{i}", evergreen=True) for t in ("big", "small") for i in range(3)]
    return {
        "big": [n for n in evergreens if n.path.startswith("big")] + [_n("bigx", date(2026, 1, 1))],
        "small": [n for n in evergreens if n.path.startswith("small")]
        + [_n("smallx", date(2026, 1, 1))],
    }


def test_find_signals_orders_by_rule_then_larger_topic_then_id(
    busy: dict[str, list[TopicNote]],
) -> None:
    signals = find_signals(busy, _defs("small", "big"), TODAY)

    assert signals == [
        Signal(
            topic_id="big",
            topic_name="BIG",
            kind="writing_more_than_reading",
            message="Writing more than reading: 3 evergreens in the last 30 days.",
            note_count=3,
        ),
        Signal(
            topic_id="small",
            topic_name="SMALL",
            kind="writing_more_than_reading",
            message="Writing more than reading: 3 evergreens in the last 30 days.",
            note_count=3,
        ),
    ]


def test_find_signals_new_topic_ranks_first_and_list_is_capped() -> None:
    fresh = [_n(f"f{i}", date(2026, 9, 7 + i), evergreen=True) for i in range(3)]
    by_topic = {
        "a": fresh,
        "b": [_n(f"b{i}", evergreen=True) for i in range(3)] + [_n("bx", date(2026, 1, 1))],
        "c": [_n(f"c{i}", evergreen=True) for i in range(3)] + [_n("cx", date(2026, 1, 1))],
    }

    signals = find_signals(by_topic, _defs("a", "b", "c"), TODAY)

    assert MAX_SIGNALS == 3
    assert [(s.topic_id, s.kind) for s in signals] == [
        ("a", "new_and_growing"),
        # Four alerts fire; topic a's writing alert is smallest, so it is the one cut.
        ("b", "writing_more_than_reading"),
        ("c", "writing_more_than_reading"),
    ]


def test_find_signals_empty_when_nothing_fires() -> None:
    assert find_signals({}, _defs("a"), TODAY) == []
