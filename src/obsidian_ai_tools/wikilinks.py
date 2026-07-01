"""Shared wikilink utilities for parsing and resolving [[wikilinks]]."""

import re

from .indexer import VaultIndex

# Regex to match [[wikilinks]] including optional |alias
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def extract_wikilinks(content: str) -> set[str]:
    """Extract wikilink targets from note content.

    Args:
        content: Markdown content

    Returns:
        Set of linked note names (without [[]] brackets)
    """
    matches = [m.strip() for m in WIKILINK_PATTERN.findall(content)]
    return set(matches)


def extract_top_wikilinks(content: str, n: int = 5) -> list[str]:
    """Extract the first N unique wikilink targets in document order.

    Args:
        content: Markdown content
        n: Maximum number of links to return

    Returns:
        List of unique wikilink targets in first-occurrence order
    """
    matches = [m.strip() for m in WIKILINK_PATTERN.findall(content)]
    return list(dict.fromkeys(matches))[:n]


def count_backlinks(vault_index: VaultIndex) -> dict[str, int]:
    """Count [[wikilink]] references across all notes.

    Args:
        vault_index: VaultIndex with all notes

    Returns:
        Dictionary mapping link target to backlink count
    """
    backlink_counts: dict[str, int] = {}

    for note in vault_index.notes:
        for link_target in WIKILINK_PATTERN.findall(note.content):
            link_target = link_target.strip().lower()
            if link_target:
                backlink_counts[link_target] = backlink_counts.get(link_target, 0) + 1

    return backlink_counts
