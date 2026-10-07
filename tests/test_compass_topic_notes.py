"""Tests for how a note's source (channel, author or site) is named (T2)."""

import pytest

from vault_compass.topic_notes import TopicNote, source_name


def _note(url: str | None, author: str | None) -> TopicNote:
    return TopicNote("p.md", "p", None, None, False, url, author, False)


@pytest.mark.parametrize(
    ("url", "author", "expected"),
    [
        # A channel beats the site, which is the same for every video.
        ("https://www.youtube.com/watch?v=1", "Some Channel", "Some Channel"),
        ("https://www.youtube.com/watch?v=1", "  Some Channel  ", "Some Channel"),
        ("https://www.site.com/a", None, "site.com"),
        ("https://site.com/a", "", "site.com"),
        ("https://site.com/a", "   ", "site.com"),
        # Placeholder authors count as unknown and fall back to the site.
        ("https://site.com/a", "Unknown", "site.com"),
        ("https://site.com/a", "Unknown Author", "site.com"),
        ("https://site.com/a", "N/A", "site.com"),
        ("https://site.com/a", "none", "site.com"),
        (None, "Zed", "Zed"),
        (None, "Unknown", None),
        (None, None, None),
        ("not a url", None, None),
    ],
)
def test_source_name(url: str | None, author: str | None, expected: str | None) -> None:
    assert source_name(_note(url, author)) == expected
