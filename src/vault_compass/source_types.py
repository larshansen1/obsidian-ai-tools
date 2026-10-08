"""Source type (study, report, essay, talk) of a web source (Q8).

Domain rules first: they match the URL's host or a parent domain, so "ft.com"
never matches "microsoft.com". A URL no rule covers is looked up in the
`source_type_cache` table, then asked of the model once and cached. Without a
model, or when the call is refused or fails, the type stays "unknown".
"""

from pathlib import Path
from urllib.parse import urlparse

import duckdb

from .db import readonly, writable
from .source_ai import SourceAi, SourceAiError

UNKNOWN = "unknown"

DOMAIN_RULES: dict[str, str] = {
    "arxiv.org": "study",
    "doi.org": "study",
    "jstor.org": "study",
    "nature.com": "study",
    "ncbi.nlm.nih.gov": "study",
    "papers.ssrn.com": "study",
    "researchgate.net": "study",
    "sciencedirect.com": "study",
    "semanticscholar.org": "study",
    "metr.org": "report",
    "bbc.com": "report",
    "bloomberg.com": "report",
    "economist.com": "report",
    "ft.com": "report",
    "nytimes.com": "report",
    "theguardian.com": "report",
    "wsj.com": "report",
    "medium.com": "essay",
    "substack.com": "essay",
    "podcasts.apple.com": "talk",
    "open.spotify.com": "talk",
    "vimeo.com": "talk",
    "youtu.be": "talk",
    "youtube.com": "talk",
}


def classify(url: str) -> str:
    """The source type for a URL, or "unknown" when no domain rule covers it."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    for domain, kind in DOMAIN_RULES.items():
        if host == domain or host.endswith("." + domain):
            return kind
    return UNKNOWN


_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS source_type_cache (
    url VARCHAR PRIMARY KEY,
    type VARCHAR NOT NULL
)
"""


def cached_types(db_path: Path, urls: list[str]) -> dict[str, str]:
    """Types the model already decided for these URLs, "unknown" included."""
    if not urls or not db_path.exists():
        return {}
    with readonly(db_path) as con:
        try:
            rows = con.execute(
                "SELECT url, type FROM source_type_cache WHERE list_contains(?, url)", [urls]
            ).fetchall()
        except duckdb.CatalogException:
            return {}  # nothing cached yet
    return {url: kind for url, kind in rows}


def cache_types(db_path: Path, types: dict[str, str]) -> None:
    if not types:
        return
    with writable(db_path) as con:
        con.execute(_CACHE_DDL)
        con.executemany(
            "INSERT OR REPLACE INTO source_type_cache VALUES (?, ?)", list(types.items())
        )


def resolve_types(
    db_path: Path, sources: dict[str, str], ai: SourceAi | None
) -> tuple[dict[str, str], str | None]:
    """URL -> type for {url: title}, plus a note when the model could not be asked."""
    types = {url: classify(url) for url in sources}
    unclear = [url for url, kind in types.items() if kind == UNKNOWN]
    cached = cached_types(db_path, unclear)
    types.update(cached)
    unclear = [url for url in unclear if url not in cached]
    if not unclear or ai is None:
        return types, None
    try:
        answered = ai.classify([(url, sources[url]) for url in unclear])
    except SourceAiError as e:
        return types, f"Source types not checked: {e}"
    # A source the model could not type is cached as unknown, so it is not asked (and paid) again.
    decided = {url: answered.get(url, UNKNOWN) for url in unclear}
    cache_types(db_path, decided)
    types.update(decided)
    return types, None
