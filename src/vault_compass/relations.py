"""Tags, links and likely duplicates between notes (D2, D5).

All functions are pure: they take what the scan already read and return rows.
Wikilink extraction is kai's own, so both tools agree on what a link is.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any

from obsidian_ai_tools.wikilinks import extract_wikilinks

# Titles at or above this similarity (0..1) count as near-identical (D5).
TITLE_SIMILARITY_THRESHOLD = 0.85

REASON_SOURCE_URL = "source_url"
REASON_TITLE = "author_title"


@dataclass(frozen=True)
class LinkRow:
    """One outgoing wikilink. target_path is None when the link is unresolved."""

    source_path: str
    target: str
    target_path: str | None

    @property
    def is_resolved(self) -> bool:
        return self.target_path is not None


@dataclass(frozen=True)
class DuplicateRow:
    """A likely duplicate pair; path_a sorts before path_b."""

    path_a: str
    path_b: str
    reason: str


def extract_tags(value: Any) -> list[str]:
    """Tags from a frontmatter `tags` value: a list, or a comma/space separated string.

    Leading "#" is dropped, blanks are skipped and repeats are kept once.
    """
    if value is None:
        return []
    if isinstance(value, str):
        items: list[Any] = re.split(r"[,\s]+", value)
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    tags: list[str] = []
    for item in items:
        tag = str(item).strip().lstrip("#").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def link_targets(body: str) -> list[str]:
    """Distinct link targets in a note body, sorted, without #heading or ^block parts."""
    targets: set[str] = set()
    for raw in extract_wikilinks(body):
        target = re.split(r"[#^]", raw, maxsplit=1)[0].strip()
        if target.lower().endswith(".md"):
            target = target[: -len(".md")]
        if target:
            targets.add(target)
    return sorted(targets)


def _link_index(paths: list[str]) -> dict[str, str]:
    """Map every trailing path piece (lowercase, no .md) to one note path.

    "a/b/c.md" answers to "c", "b/c" and "a/b/c". When several notes answer to
    the same name the shortest path wins, then alphabetical (Obsidian's rule).
    """
    index: dict[str, str] = {}
    for path in sorted(paths, key=lambda p: (len(p), p)):
        parts = path[: -len(".md")].lower().split("/")
        for i in range(len(parts)):
            index.setdefault("/".join(parts[i:]), path)
    return index


def resolve_links(notes: dict[str, list[str]]) -> list[LinkRow]:
    """Resolve each note's link targets to note paths.

    `notes` maps a note path to its link targets. A link appears once per
    source: resolved links are keyed by target path, unresolved by target text.
    """
    index = _link_index(list(notes))
    rows: list[LinkRow] = []
    for source in sorted(notes):
        seen: set[str] = set()
        for target in notes[source]:
            target_path = index.get(target.lower())
            key = target_path if target_path is not None else "?" + target.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append(LinkRow(source, target, target_path))
    return rows


def _title_key(title: str) -> str:
    return " ".join(re.findall(r"\w+", title.lower()))


def find_duplicates(
    notes: list[tuple[str, str, str | None, str | None]],
) -> list[DuplicateRow]:
    """Likely duplicates from (path, title, source_url, author) tuples.

    Same source URL wins over a title match when a pair has both.
    """
    pairs: dict[tuple[str, str], str] = {}

    by_url: dict[str, list[str]] = defaultdict(list)
    for path, _title, url, _author in notes:
        if url and url.strip():
            by_url[url.strip().rstrip("/")].append(path)
    for paths in by_url.values():
        for a, b in combinations(sorted(paths), 2):
            pairs[(a, b)] = REASON_SOURCE_URL

    by_author: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path, title, _url, author in notes:
        key = _title_key(title)
        if author and author.strip() and key:
            by_author[author.strip().lower()].append((path, key))
    for group in by_author.values():
        for (path_x, key_x), (path_y, key_y) in combinations(sorted(group), 2):
            if (path_x, path_y) in pairs:
                continue
            if SequenceMatcher(None, key_x, key_y).ratio() >= TITLE_SIMILARITY_THRESHOLD:
                pairs[(path_x, path_y)] = REASON_TITLE

    return [DuplicateRow(a, b, reason) for (a, b), reason in sorted(pairs.items())]
