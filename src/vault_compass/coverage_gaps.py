"""Coverage gaps: sources cited in notes but not yet ingested (Q9, C7).

Find URLs mentioned in a topic's notes that don't have source_url in the vault yet.
These are the gaps to fill with new sources, prioritizing citations already in notes.

Every source shown is verified to exist online before display (C7).
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from .ai_policy import is_excluded
from .db import readonly
from .source_types import get_or_classify

logger = logging.getLogger(__name__)

# Regex to find URLs in text
URL_PATTERN = re.compile(r"https?://[^\s\)\]\}\"'`<>]+")


@dataclass(frozen=True)
class CandidateSource:
    """A source candidate to fill a gap."""

    url: str
    title: str
    source_type: str
    from_citations: bool  # True if from existing notes, False if from web search
    note_path: str | None = None  # If from citations, the note that cited it
    verified: bool = False  # True if URL was confirmed to exist online


def verify_url_exists(url: str, timeout: int = 10) -> bool:
    """Verify that a URL is reachable online.

    Returns True if the URL responds with a non-404 status code.
    Returns False if unreachable or not HTTP(S).
    """
    if not requests:
        logger.warning("requests library not available, skipping URL verification")
        return True  # Assume it exists if we can't verify

    if not url.startswith(("http://", "https://")):
        return False

    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        # Accept 2xx, 3xx responses as "exists"
        return 200 <= response.status_code < 400
    except requests.RequestException:
        # Try GET as fallback (some servers don't support HEAD)
        try:
            response = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
            return 200 <= response.status_code < 400
        except requests.RequestException as e:
            logger.debug(f"URL verification failed for {url}: {e}")
            return False


def extract_urls_from_text(text: str) -> set[str]:
    """Extract URLs from note text."""
    urls = set()
    for match in URL_PATTERN.finditer(text):
        url = match.group(0)
        # Clean up trailing punctuation that isn't part of URL
        url = re.sub(r"[.,;:!?)\]\}\"'`]*$", "", url)
        urls.add(url)
    return urls


def find_coverage_gaps(
    vault_path: Path, db_path: Path, topic: str, definitions: Any
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """
    Find coverage gaps for a topic.

    Returns:
        (gaps_by_note: dict of note_path -> [urls cited but not in vault],
         citation_sources: dict of url -> note_path for citations)
    """
    gaps_by_note = {}
    citation_sources = {}

    try:
        with readonly(db_path) as con:
            # Get all notes in this topic
            notes_result = con.execute(
                """
                SELECT DISTINCT n.path, n.title
                FROM notes n
                WHERE n.path IN (SELECT path FROM note_topics WHERE topic = ?)
                """,
                [topic],
            ).fetchall()

            for note_path, _note_title in notes_result:
                # Skip excluded folders
                if is_excluded(note_path, definitions):
                    continue

                # Read the note file
                note_file = vault_path / note_path
                if not note_file.exists():
                    continue

                try:
                    with open(note_file, encoding="utf-8") as f:
                        content = f.read()

                    # Extract URLs from note
                    urls = extract_urls_from_text(content)

                    if not urls:
                        continue

                    # Check which URLs are already in the vault as source_url
                    if urls:
                        placeholders = ",".join("?" * len(urls))
                        ingested = con.execute(
                            f"SELECT source_url FROM notes WHERE source_url IN ({placeholders})",  # nosec B608
                            list(urls),
                        ).fetchall()
                    else:
                        ingested = []
                    ingested_urls = {row[0] for row in ingested}

                    # URLs mentioned but not ingested = gaps
                    gaps = urls - ingested_urls
                    if gaps:
                        gaps_by_note[note_path] = sorted(gaps)
                        for url in gaps:
                            if url not in citation_sources:
                                citation_sources[url] = note_path

                except (UnicodeDecodeError, OSError) as e:
                    logger.warning(f"Could not read {note_path}: {e}")
                    continue

    except Exception as e:
        logger.error(f"Failed to find coverage gaps for topic {topic}: {e}")
        return {}, {}

    return gaps_by_note, citation_sources


def build_candidates_from_citations(
    vault_path: Path,
    db_path: Path,
    topic: str,
    definitions: Any,
    verify: bool = True,
) -> list[CandidateSource]:
    """Build candidate sources from citations in topic notes.

    By default, verifies that all sources exist online before returning them (C7).
    Set verify=False to skip verification (useful for testing).
    """
    gaps, citation_sources = find_coverage_gaps(vault_path, db_path, topic, definitions)

    candidates = []
    for url, note_path in citation_sources.items():
        # Verify URL exists online before adding to candidates
        if verify and not verify_url_exists(url):
            logger.info(f"Skipping candidate {url}: not reachable online")
            continue

        # Extract title from URL or note context
        title = _extract_title_from_url(url, vault_path, note_path)
        source_type = get_or_classify(url, title, db_path)

        candidates.append(
            CandidateSource(
                url=url,
                title=title,
                source_type=source_type,
                from_citations=True,
                note_path=note_path,
                verified=verify,
            )
        )

    return candidates


def _extract_title_from_url(url: str, vault_path: Path, note_path: str) -> str:
    """Extract a title from the note that cited it, or URL domain."""
    # Try: use the note that cited it
    try:
        note_file = vault_path / note_path
        if note_file.exists():
            with open(note_file, encoding="utf-8") as f:
                content = f.read()
            # Find line containing the URL and extract context
            for line in content.split("\n"):
                if url in line:
                    # Strip markdown link syntax and markdown formatting
                    title = (
                        line.replace("[", "")
                        .replace("]", "")
                        .replace(f"({url})", "")
                        .replace(url, "")
                        .strip()
                    )
                    if title and len(title) < 200:
                        return title
    except (UnicodeDecodeError, OSError, KeyError, IndexError):
        # Unable to extract title from note context; will fall back to domain
        pass

    # Fallback: extract domain from URL
    try:
        from urllib.parse import urlparse

        domain = urlparse(url).netloc
        return domain or url
    except (ValueError, TypeError, AttributeError):
        # URL parsing failed; return URL as-is
        return url
