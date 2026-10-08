"""Coverage gaps and source candidates for a topic (C7, Q9).

A gap is a web source a topic's notes cite that is not in the vault yet (no
note has it as its source_url). Note text is read only through a `read_body`
callable (AiVault.body), so excluded folders never contribute (N4).

Candidates are citation gaps first, then web search results when citations run
out. Each one must be confirmed online (C7). The check makes network calls, so
callers must run it after closing their DuckDB connection.
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

from .source_ai import SourceAiError, WebHit

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_S = 10
MAX_CANDIDATES = 10
# Several sites refuse the default python-requests agent with 403.
USER_AGENT = "Mozilla/5.0 (compatible; VaultCompass/1.0)"

_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", re.I)
_BARE_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)
_TRAILING = re.compile(r"[.,;:!?]+$")
# PubMed Central refuses every automated page request (403, even for fake IDs),
# so its articles are checked with NCBI's E-utilities lookup instead.
_PMC = re.compile(r"(?:pmc\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pmc)/articles/PMC(\d+)", re.I)
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
# YouTube answers 200 for any watch link, even a made-up one; its oEmbed service does not.
_YOUTUBE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch|shorts/)|youtu\.be/)", re.I
)
OEMBED_URL = "https://www.youtube.com/oembed"
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
    # The note that cites it; None for a web search result.
    note_path: str | None


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


def ingested_keys(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {_key(row[0]) for row in con.execute(_INGESTED_SQL).fetchall()}


def find_gaps(
    con: duckdb.DuckDBPyConnection, topic: str, read_body: Callable[[str], str | None]
) -> list[Gap]:
    """Sources cited by the topic's notes and not ingested, first citation wins.

    Links under a note's own source_url are part of that source, not new ones.
    """
    ingested = ingested_keys(con)
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


def pmc_exists(pmcid: str, timeout: float = VERIFY_TIMEOUT_S) -> bool:
    """True when NCBI knows the PubMed Central article (fake IDs come back with an error)."""
    try:
        response = requests.get(
            ESUMMARY_URL,
            params={"db": "pmc", "id": pmcid, "retmode": "json"},
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        entry = response.json()["result"][pmcid]
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.debug("PMC lookup for %s failed: %s", pmcid, e)
        return False
    return response.status_code == 200 and isinstance(entry, dict) and "error" not in entry


def youtube_exists(url: str, timeout: float = VERIFY_TIMEOUT_S) -> bool:
    """True when YouTube's oEmbed service knows the video."""
    try:
        response = requests.get(
            OEMBED_URL,
            params={"url": url, "format": "json"},
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as e:
        logger.debug("YouTube lookup for %s failed: %s", url, e)
        return False
    return response.status_code == 200


def _answers(url: str, timeout: float) -> bool:
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


def url_exists(url: str, timeout: float = VERIFY_TIMEOUT_S) -> bool:
    """True when the source exists online.

    PubMed Central articles and YouTube videos are looked up; anything else must
    answer below 400 to HEAD or, failing that, GET (some sites refuse HEAD).
    """
    match = _PMC.search(url)
    if match:
        return pmc_exists(match.group(1), timeout)
    if _YOUTUBE.match(url):
        return youtube_exists(url, timeout)
    return _answers(url, timeout)


def summarize_gaps(topic: str, gaps: list[Gap], types: dict[str, str]) -> dict[str, Any]:
    """Counts for the coverage bars. No URLs: unverified sources are never shown (C7)."""
    by_type = Counter(types[g.url] for g in gaps)
    return {
        "topic": topic,
        "count": len(gaps),
        "notes": len({g.note_path for g in gaps}),
        "by_type": [
            {"type": kind, "count": n}
            for kind, n in sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def web_gaps(hits: list[WebHit], skip: set[str]) -> list[Gap]:
    """Search results not in the vault and not already cited, one per source."""
    seen = set(skip)
    gaps = []
    for hit in hits:
        key = _key(hit.url)
        if key not in seen:
            seen.add(key)
            gaps.append(Gap(hit.url, hit.title, None))
    return gaps


def verify(gaps: list[Gap], exists: Callable[[str], bool], limit: int) -> tuple[list[Gap], int]:
    """Up to `limit` gaps confirmed online, and how many failed the check."""
    kept: list[Gap] = []
    dropped = 0
    for gap in gaps:
        if len(kept) == limit:
            break
        if exists(gap.url):
            kept.append(gap)
        else:
            logger.info("Dropped source candidate %s: not found online", gap.url)
            dropped += 1
    return kept, dropped


def find_candidates(
    citations: list[Gap],
    skip: set[str],
    search: Callable[[], list[WebHit]] | None,
    exists: Callable[[str], bool] = url_exists,
    limit: int = MAX_CANDIDATES,
) -> tuple[list[Gap], int, list[str]]:
    """Citations first; web results only fill the places citations leave (Q9).

    Returns the confirmed candidates, how many failed the online check, and notes
    for the model about anything skipped.
    """
    shown, not_found = verify(citations, exists, limit)
    if len(shown) == limit:
        return shown, not_found, []
    if search is None:
        return shown, not_found, ["Web search is not available here (no OpenRouter key)."]
    try:
        hits = search()
    except SourceAiError as e:
        return shown, not_found, [f"Web search skipped: {e}"]
    seen = skip | {_key(g.url) for g in citations}
    more, dropped = verify(web_gaps(hits, seen), exists, limit - len(shown))
    return shown + more, not_found + dropped, []


def card(gap: Gap, kind: str, topic_name: str) -> dict[str, str | None]:
    if gap.note_path is None:
        origin, why = "web", f"Found by web search on {topic_name}; not in the vault yet."
    else:
        origin, why = "citation", f"Cited in [[{gap.note_path}]] but not in the vault yet."
    return {
        "url": gap.url,
        "title": gap.title,
        "type": kind,
        "origin": origin,
        "cited_in": gap.note_path,
        "why": why,
    }
