"""Source type (study, report, essay, talk) of a web source, from domain rules (Q8).

Rules match the URL's host or a parent domain, so "ft.com" never matches
"microsoft.com". A URL no rule covers is "unknown"; the LLM fallback from Q8
is not built yet.
"""

from urllib.parse import urlparse

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
