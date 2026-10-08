"""Source type classification (study, report, essay, talk) cached in compass.duckdb.

Determines type from source_type field and domain rules first; LLM only when unclear (Q8).
Never rewrite notes. Types are cached and reused across calls.
"""

import logging
from typing import Any

from .db import readonly, writable

logger = logging.getLogger(__name__)

SOURCE_TYPES_DDL = """
CREATE TABLE IF NOT EXISTS source_types (
    url VARCHAR PRIMARY KEY,
    type VARCHAR NOT NULL,
    classified_by VARCHAR NOT NULL,
    cached_at TIMESTAMP DEFAULT current_timestamp
)
"""

DOMAIN_RULES = {
    "arxiv.org": "study",
    "researchgate.net": "study",
    "scholar.google.com": "study",
    "jstor.org": "study",
    "semanticscholar.org": "study",
    "papers.ssrn.com": "study",
    "github.com": "report",
    "linkedin.com": "report",
    "medium.com": "essay",
    "substack.com": "essay",
    "twitter.com": "talk",
    "youtube.com": "talk",
    "youtu.be": "talk",
    "podcasts.google.com": "talk",
    "spotify.com": "talk",
    "news.ycombinator.com": "report",
    "reddit.com": "report",
    "theguardian.com": "report",
    "bbc.com": "report",
    "economist.com": "report",
    "wsj.com": "report",
    "ft.com": "report",
    "bloomberg.com": "report",
    "cnn.com": "report",
    "nytimes.com": "report",
}


def classify_from_domain(url: str) -> str | None:
    """Classify source type based on domain."""
    lower_url = url.lower()
    for domain, type_name in DOMAIN_RULES.items():
        if domain in lower_url:
            return type_name
    return None


def get_or_classify(url: str, title: str = "", db_path: Any = None) -> str:
    """Get cached source type or determine it."""
    # First check cache
    if db_path:
        try:
            with readonly(db_path) as con:
                result = con.execute(
                    "SELECT type FROM source_types WHERE url = ?", [url]
                ).fetchall()
                if result:
                    return str(result[0][0])
        except (OSError, TypeError, IndexError):
            # Cache lookup failed; will classify from domain rules instead
            pass

    # Try domain rules
    type_name = classify_from_domain(url)
    if type_name:
        return type_name

    # Default to "report" if no domain match
    return "report"


def cache_source_type(url: str, type_name: str, classified_by: str, db_path: Any) -> None:
    """Cache a source type classification."""
    try:
        with writable(db_path) as con:
            con.execute(SOURCE_TYPES_DDL)
            con.execute(
                "INSERT OR REPLACE INTO source_types (url, type, classified_by) VALUES (?, ?, ?)",
                [url, type_name, classified_by],
            )
    except Exception as e:
        logger.warning(f"Failed to cache source type for {url}: {e}")


def init_source_types_table(db_path: Any) -> None:
    """Initialize source_types table if it doesn't exist."""
    with writable(db_path) as con:
        con.execute(SOURCE_TYPES_DDL)
