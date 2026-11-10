"""Numbers for one topic page: header, sources, evergreens, recent notes (T1, T2, T5, T6).

The header is the same TopicStats the topic map builds, so the counts match.
Reads only; `today` is passed in so the result is reproducible.
"""

from collections import Counter
from datetime import date

import duckdb
from pydantic import BaseModel

from .signals import SIGNAL_RULES, SignalKind, topic_signal_notes
from .topic_map import TopicStats, Window, topic_stats
from .topic_notes import TopicNote, load_topic_notes, source_name
from .topics import TopicsFile

RECENT_NOTES_LIMIT = 20
TOP_SOURCES_LIMIT = 5
UNKNOWN_SOURCE_TYPE = "unknown"

_NOTE_TOPICS_SQL = "SELECT path, topic FROM note_topics"
# Which of the topic's tags each note carries: why the note is in the topic (#189).
_MATCHED_TAGS_SQL = """
SELECT nt.path, nt.tag
FROM note_tags nt JOIN topic_tags tt ON tt.tag = nt.tag
WHERE tt.topic = ?
"""
_NOTE_REFS_SQL = "SELECT path, title, created FROM notes"
_DUPLICATES_SQL = "SELECT path_a, path_b, reason FROM duplicates"


class NoteRef(BaseModel):
    path: str
    title: str
    created: date | None


class SourceCount(BaseModel):
    name: str
    notes: int


class SourceTypeShare(BaseModel):
    source_type: str
    notes: int
    # Fraction 0..1 of the topic's notes.
    share: float


class Bridge(BaseModel):
    id: str
    name: str


class EvergreenNote(NoteRef):
    # Other topics this evergreen is in; empty unless it is in 2 or more topics.
    bridges: list[Bridge]


class Duplicate(BaseModel):
    path: str
    reason: str


class RecentNote(NoteRef):
    duplicates: list[Duplicate]
    # The topic's tags this note carries, in the topic's own order.
    matched_tags: list[str]


class MatchedNote(NoteRef):
    matched_tags: list[str]


class SignalNotes(BaseModel):
    kind: SignalKind
    rules: str
    notes: list[NoteRef]


class TopicPageResponse(BaseModel):
    window: Window
    min_notes: int
    stats: TopicStats
    tags: list[str]
    top_sources: list[SourceCount]
    # Notes with neither a source link nor an author. Counted, not hidden.
    unknown_source_notes: int
    source_types: list[SourceTypeShare]
    evergreens: list[EvergreenNote]
    recent_notes: list[RecentNote]
    # Notes that are here through one tag only, newest first: the ones to check when a broad
    # tag pulls in notes that do not belong.
    single_tag_notes: list[MatchedNote]
    # Notes taken out of the topic by hand (the topic's `exclude` list), in file order.
    excluded_notes: list[NoteRef]
    # Set when the page was opened from a signal on the topic map (M6).
    signal: SignalNotes | None


def _ref(note: TopicNote) -> NoteRef:
    return NoteRef(path=note.path, title=note.title, created=note.created)


def _newest_first(notes: list[TopicNote]) -> list[TopicNote]:
    dated = sorted((n for n in notes if n.created is not None), key=lambda n: n.path)
    dated.sort(key=lambda n: n.created or date.min, reverse=True)
    return dated + sorted((n for n in notes if n.created is None), key=lambda n: n.path)


def _source_types(notes: list[TopicNote]) -> list[SourceTypeShare]:
    counts = Counter(n.source_type or UNKNOWN_SOURCE_TYPE for n in notes)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [SourceTypeShare(source_type=t, notes=c, share=c / len(notes)) for t, c in ordered]


def _bridges(
    paths: list[str], topic_id: str, definitions: TopicsFile, con: duckdb.DuckDBPyConnection
) -> dict[str, list[Bridge]]:
    wanted = set(paths)
    topics_of: dict[str, set[str]] = {}
    for path, topic in con.execute(_NOTE_TOPICS_SQL).fetchall():
        if path in wanted:
            topics_of.setdefault(path, set()).add(topic)
    result: dict[str, list[Bridge]] = {}
    for path in paths:
        others = topics_of.get(path, set()) - {topic_id}
        result[path] = [
            Bridge(id=tid, name=t.name) for tid, t in definitions.topics.items() if tid in others
        ]
    return result


def _duplicates(con: duckdb.DuckDBPyConnection) -> dict[str, list[Duplicate]]:
    partners: dict[str, list[Duplicate]] = {}
    for path_a, path_b, reason in con.execute(_DUPLICATES_SQL).fetchall():
        partners.setdefault(path_a, []).append(Duplicate(path=path_b, reason=reason))
        partners.setdefault(path_b, []).append(Duplicate(path=path_a, reason=reason))
    for items in partners.values():
        items.sort(key=lambda d: (d.path, d.reason))
    return partners


def _matched_tags(
    con: duckdb.DuckDBPyConnection, topic_id: str, tags: list[str]
) -> dict[str, list[str]]:
    order = {tag: i for i, tag in enumerate(tags)}
    matched: dict[str, list[str]] = {}
    for path, tag in con.execute(_MATCHED_TAGS_SQL, [topic_id]).fetchall():
        matched.setdefault(path, []).append(tag)
    for found in matched.values():
        found.sort(key=lambda tag: order.get(tag, len(order)))
    return matched


def _excluded(con: duckdb.DuckDBPyConnection, paths: list[str]) -> list[NoteRef]:
    """The excluded notes; one that is no longer in the vault keeps its path as its title."""
    known = {
        path: (title, created) for path, title, created in con.execute(_NOTE_REFS_SQL).fetchall()
    }
    return [
        NoteRef(path=p, title=known[p][0], created=known[p][1])
        if p in known
        else NoteRef(path=p, title=p, created=None)
        for p in paths
    ]


def build_topic_page(
    con: duckdb.DuckDBPyConnection,
    definitions: TopicsFile,
    topic_id: str,
    *,
    today: date,
    window: Window,
    signal: SignalKind | None = None,
) -> TopicPageResponse:
    """Raises KeyError when the topic id is not in the definitions."""
    topic = definitions.topics[topic_id]
    notes = load_topic_notes(con).get(topic_id, [])
    stats = topic_stats(
        topic_id, topic.name, notes, definitions=definitions, today=today, window=window
    )

    names = Counter(name for n in notes if (name := source_name(n)) is not None)
    top = sorted(names.items(), key=lambda item: (-item[1], item[0]))[:TOP_SOURCES_LIMIT]

    evergreens = _newest_first([n for n in notes if n.is_evergreen])
    bridges = _bridges([n.path for n in evergreens], topic_id, definitions, con)
    partners = _duplicates(con)
    matched = _matched_tags(con, topic_id, topic.tags)

    signal_notes = None
    if signal is not None:
        behind = topic_signal_notes(signal, notes, definitions, today)
        signal_notes = SignalNotes(
            kind=signal, rules=SIGNAL_RULES, notes=[_ref(n) for n in _newest_first(behind)]
        )

    return TopicPageResponse(
        window=window,
        min_notes=definitions.min_notes,
        stats=stats,
        tags=topic.tags,
        top_sources=[SourceCount(name=name, notes=count) for name, count in top],
        unknown_source_notes=sum(1 for n in notes if source_name(n) is None),
        source_types=_source_types(notes) if notes else [],
        evergreens=[
            EvergreenNote(**_ref(n).model_dump(), bridges=bridges[n.path]) for n in evergreens
        ],
        recent_notes=[
            RecentNote(
                **_ref(n).model_dump(),
                duplicates=partners.get(n.path, []),
                matched_tags=matched.get(n.path, []),
            )
            for n in _newest_first(notes)[:RECENT_NOTES_LIMIT]
        ],
        single_tag_notes=[
            MatchedNote(**_ref(n).model_dump(), matched_tags=matched[n.path])
            for n in _newest_first(notes)
            if len(matched.get(n.path, [])) == 1
        ],
        excluded_notes=_excluded(con, topic.exclude),
        signal=signal_notes,
    )
