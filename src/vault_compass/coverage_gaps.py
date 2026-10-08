"""Coverage gaps and source candidates for a topic (C7, Q9).

A gap is a web source a topic's notes cite that is not in the vault yet (no
note has it as its source_url). Note text is read only through a `read_body`
callable (AiVault.body), so excluded folders never contribute (N4).

Candidates are gaps that were confirmed to exist online (C7). The check makes
network calls, so callers must run it after closing their DuckDB connection.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import duckdb
import requests

from .source_types import classify

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_S = 10
MAX_CANDIDATES = 10
# Several sites refuse the default python-requests agent with 403.
USER_AGENT = "Mozilla/5.0 (compatible; VaultCompass/1.0)"

_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", re.I)
_BARE_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)
_TRAILING = re.compile(r"[.,;:!?]+$")
_TOPIC_NOTES_SQL = """
SELECT DISTINCT t.path, n.source_url
FROM note_topics t JOIN notes n ON n.path = t.path
WHERE t.topic = ?
ORDER BY t.path
"""
_INGESTED_SQL = "SELECT source_url FROM notes WHERE source_url IS NOT NULL"


@dataclass(frozen=True)
class Gap:
    url: str
    title: str
    note_path: str


def _clean(url: str) -> str:
    return _TRAILING.sub("", url)


def _base(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _key(url: str) -> str:
    """Comparison form: scheme and host case and a trailing slash don't make a new source."""
    return f"{_base(url)}?{urlparse(url).query}"


def _inside(url: str, source_url: str | None) -> bool:
    """True for a page under the note's own source, such as a README in the same repo."""
    return bool(source_url) and _base(url).startswith(_base(source_url or "") + "/")


def extract_citations(body: str) -> dict[str, str]:
    """URL -> title for each web link in a note body, in order of appearance.

    A Markdown link's text is its title; a bare URL is titled by its host.
    """
    found: dict[str, str] = {}
    for text, url in _MD_LINK.findall(body):
        found.setdefault(_clean(url), text.strip())
    for match in _BARE_URL.finditer(body):
        url = _clean(match.group(0))
        found.setdefault(url, urlparse(url).netloc)
    return found


def find_gaps(
    con: duckdb.DuckDBPyConnection, topic: str, read_body: Callable[[str], str | None]
) -> list[Gap]:
    """Sources cited by the topic's notes and not ingested, first citation wins.

    Links under a note's own source_url are part of that source, not new ones.
    """
    ingested = {_key(row[0]) for row in con.execute(_INGESTED_SQL).fetchall()}
    gaps: dict[str, Gap] = {}
    for path, source_url in con.execute(_TOPIC_NOTES_SQL, [topic]).fetchall():
        body = read_body(path)
        if body is None:
            continue
        for url, title in extract_citations(body).items():
            key = _key(url)
            if key not in ingested and key not in gaps and not _inside(url, source_url):
                gaps[key] = Gap(url, title, path)
    return list(gaps.values())


def url_exists(url: str, timeout: float = VERIFY_TIMEOUT_S) -> bool:
    """True when the URL answers below 400, trying HEAD and then GET (some sites refuse HEAD)."""
    for method in ("HEAD", "GET"):
        try:
            response = requests.request(
                method,
                url,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as e:
            logger.debug("%s %s failed: %s", method, url, e)
            continue
        response.close()
        if response.status_code < 400:
            return True
    return False


def summarize_gaps(topic: str, gaps: list[Gap]) -> dict[str, Any]:
    """Counts for the coverage bars. No URLs: unverified sources are never shown (C7)."""
    by_type = Counter(classify(g.url) for g in gaps)
    return {
        "topic": topic,
        "count": len(gaps),
        "notes": len({g.note_path for g in gaps}),
        "by_type": [
            {"type": kind, "count": n}
            for kind, n in sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def pick_candidates(
    gaps: list[Gap],
    exists: Callable[[str], bool] = url_exists,
    limit: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    """Up to `limit` gaps confirmed online, as cards; gaps that fail the check are counted only."""
    cards: list[dict[str, str]] = []
    not_found = 0
    for gap in gaps:
        if len(cards) == limit:
            break
        if not exists(gap.url):
            logger.info("Dropped source candidate %s: not found online", gap.url)
            not_found += 1
            continue
        cards.append(
            {
                "url": gap.url,
                "title": gap.title,
                "type": classify(gap.url),
                "origin": "citation",
                "cited_in": gap.note_path,
                "why": f"Cited in [[{gap.note_path}]] but not in the vault yet.",
            }
        )
    return {"candidates": cards, "not_found": not_found}
