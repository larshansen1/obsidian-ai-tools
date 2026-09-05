"""Source-based duplicate detection for the ingestion pipeline.

Notes are reconciled by the source_url stored in their frontmatter, never by
title: LLM-generated titles differ between runs for the same source. URLs are
normalized before comparison so trivially different forms of the same source
(tracking params, youtu.be vs. youtube.com/watch) do not create duplicates.
"""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

# Query parameters that identify a marketing campaign or share event, not the
# content itself.
_TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "si", "ref_src"}

_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com"}


@dataclass(frozen=True)
class ExistingNote:
    """A vault note that matches an ingestion source."""

    file_path: Path
    title: str
    tags: list[str]
    source_type: str | None


def _youtube_video_id(host: str, path: str, query_pairs: list[tuple[str, str]]) -> str | None:
    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
        return video_id or None
    if host in _YOUTUBE_HOSTS:
        segments = [s for s in path.split("/") if s]
        if path == "/watch" or path == "/watch/":
            return dict(query_pairs).get("v")
        if len(segments) == 2 and segments[0] in ("shorts", "embed", "live", "v"):
            return segments[1]
    return None


def normalize_source_url(url: str) -> str:
    """Reduce a URL to a stable comparison key.

    Non-HTTP sources (local file paths) are only whitespace-trimmed.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url

    host = parsed.netloc.lower()
    host = host.removeprefix("www.")
    host = host.removesuffix(":80").removesuffix(":443")

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in _TRACKING_PARAMS and not k.startswith("utm_")
    ]

    video_id = _youtube_video_id(host, parsed.path, query_pairs)
    if video_id is not None:
        return f"youtube.com/watch?v={video_id}"

    path = parsed.path.rstrip("/")
    query = urlencode(sorted(query_pairs)) if query_pairs else ""
    return f"{host}{path}" + (f"?{query}" if query else "")


def find_note_by_source(vault_path: Path, url: str) -> ExistingNote | None:
    """Find the first vault note whose frontmatter source_url matches url.

    Reads only frontmatter blocks and parses YAML only for the matching file,
    so the scan stays cheap relative to the fetch + LLM pipeline it guards.
    """
    from ._vault_store import VaultStore

    target = normalize_source_url(url)
    for md_file in sorted(vault_path.rglob("*.md")):
        relative_parts = md_file.relative_to(vault_path).parts
        if any(part.startswith(".") for part in relative_parts):
            continue
        try:
            metadata, _content = VaultStore.parse_frontmatter(md_file)
        except Exception:
            continue
        if not isinstance(metadata, dict):
            metadata = {}
        candidate = metadata.get("source_url")
        if candidate is None or normalize_source_url(str(candidate)) != target:
            continue
        raw_tags = metadata.get("tags") or []
        tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
        source_type = metadata.get("source_type")
        return ExistingNote(
            file_path=md_file,
            title=str(metadata.get("title") or md_file.stem),
            tags=tags,
            source_type=str(source_type) if source_type is not None else None,
        )
    return None
