"""The notes under each topic, loaded once and shared by the topic map and topic page."""

from datetime import date
from typing import NamedTuple
from urllib.parse import urlparse

import duckdb

_TOPIC_NOTES_SQL = """
SELECT
    nt.topic,
    n.path,
    n.title,
    n.created,
    n.source_type,
    n.is_evergreen,
    n.source_url,
    n.author,
    EXISTS (SELECT 1 FROM links l WHERE l.source_path = n.path) AS has_link
FROM note_topics nt
JOIN notes n ON n.path = nt.path
ORDER BY nt.topic, n.path
"""


class TopicNote(NamedTuple):
    path: str
    title: str
    created: date | None
    source_type: str | None
    is_evergreen: bool
    source_url: str | None
    author: str | None
    has_link: bool


UNKNOWN_AUTHORS = frozenset({"unknown", "unknown author", "n/a", "none"})


def source_name(note: TopicNote) -> str | None:
    """The channel or author a note came from, else its site; None when neither is known.

    The author comes first: for a video the site is always the same, the channel is not.
    """
    author = (note.author or "").strip()
    if author and author.lower() not in UNKNOWN_AUTHORS:
        return author
    if note.source_url:
        host = urlparse(note.source_url).hostname
        if host:
            return host.removeprefix("www.")
    return None


def load_topic_notes(con: duckdb.DuckDBPyConnection) -> dict[str, list[TopicNote]]:
    """Every note under every topic it belongs to, ordered by path."""
    by_topic: dict[str, list[TopicNote]] = {}
    for topic, *fields in con.execute(_TOPIC_NOTES_SQL).fetchall():
        path, title, created, source_type, is_evergreen, source_url, author, has_link = fields
        by_topic.setdefault(topic, []).append(
            TopicNote(
                path,
                title,
                created,
                source_type,
                bool(is_evergreen),
                source_url,
                author,
                bool(has_link),
            )
        )
    return by_topic
