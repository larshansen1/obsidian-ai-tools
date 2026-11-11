"""Read links out of daily briefing files (#197).

A briefing file holds the job's prompt, then a `## Response` heading, then the answer. Only the
answer is read: the prompt carries example links that must not show up. Everything here is plain
text rules, no model call. Briefing text is untrusted (the agent that wrote it reads the open
web), so only a URL, a title, a date and a section heading leave this module, and each is
cleaned. Titles are shown as plain text and never rendered as Markdown or HTML.

Compass will later make requests to these URLs, so loopback and private-network hosts are refused
here. `blocked_host` is the shared test; the DNS check lives in briefings.py.
"""

import ipaddress
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .coverage_gaps import _clean

MAX_FILE_BYTES = 1_000_000
MAX_TITLE = 300
MAX_SECTION = 200

_RESPONSE = re.compile(r"^#{1,6}\s*Response\s*$", re.I | re.M)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
# The URL part allows one level of balanced parentheses (Wikipedia).
_MD_LINK = re.compile(
    r"(?<!!)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\((https?://[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)\)", re.I
)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^\s()]*(?:\([^\s()]*\)[^\s()]*)*\)")
_BARE_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)
_BOLD = re.compile(r"(?:\*\*|__)(.+?)(?:\*\*|__)")
_FILE_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid")
# Link texts that say nothing about the page: the bold item title is used instead.
_GENERIC = {
    "source",
    "sources",
    "link",
    "here",
    "read more",
    "more",
    "article",
    "read",
    "full article",
    "details",
    "via",
}
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".localdomain", ".home.arpa")


@dataclass(frozen=True)
class BriefingLink:
    """One link as one briefing file mentions it."""

    url: str
    title: str
    section: str | None
    briefing_date: date
    file: str


def blocked_host(host: str) -> bool:
    """True for a host Compass must never request: loopback, private network, local names."""
    host = host.strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return not ip.is_global or ip.is_multicast
    # A name with no dot is an intranet host. A number-like last label is an IP in a form
    # browsers accept (2130706433, 127.1, 0x7f.0.0.1), never a real domain.
    if "." not in host:
        return True
    last = host.rsplit(".", 1)[1]
    return last.isdigit() or last.startswith("0x")


def clean_url(url: str) -> str | None:
    """The link in comparison-friendly form, or None when it must not be used.

    Only http and https, no credentials, no loopback or private host. Tracking parameters and the
    fragment are dropped so the same page in two briefings is one link. Google News redirect links
    are dropped: they do not open.
    """
    url = _clean(url.strip())
    try:
        parts = urlparse(url)
        parts.port  # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        return None
    host = parts.hostname or ""
    if parts.scheme.lower() not in ("http", "https") or parts.username is not None:
        return None
    if blocked_host(host):
        return None
    if host.lower().rstrip(".") == "news.google.com" and "/articles/" in parts.path:
        return None
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING)
    ]
    return urlunparse(parts._replace(query=urlencode(query), fragment=""))


def _text(value: str, limit: int) -> str:
    """Plain text: emphasis marks and control characters gone, spaces folded, length capped."""
    value = _CONTROL.sub(" ", value).replace("**", "").replace("__", "").replace("`", "")
    return " ".join(value.split())[:limit]


def _generic(text: str, url: str) -> bool:
    plain = text.strip().strip("()[]").lower()
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return (
        len(plain) < 3
        or plain in _GENERIC
        or plain.startswith("http")
        or plain.removeprefix("www.") == host
    )


def _title(link_text: str, line: str, url: str) -> str:
    bold = _BOLD.search(line)
    if not _generic(_text(link_text, MAX_TITLE), url):
        return _text(link_text, MAX_TITLE)
    if bold and not _generic(_text(bold.group(1), MAX_TITLE), url):
        return _text(bold.group(1), MAX_TITLE)
    return _text(link_text, MAX_TITLE) or (urlparse(url).hostname or "").removeprefix("www.")


def _line_links(line: str) -> list[tuple[str, str]]:
    """(link text, URL) for each web link on the line; a bare URL has no text."""
    line = _IMAGE.sub(" ", line)
    found = [(m.group(1), m.group(2)) for m in _MD_LINK.finditer(line)]
    rest = _MD_LINK.sub(" ", line)
    found.extend(("", m.group(0)) for m in _BARE_URL.finditer(rest))
    return found


def parse_briefing(text: str, briefing_date: date, file: str) -> list[BriefingLink]:
    """Links in the answer part of one briefing. A file with no `## Response` has none."""
    marker = _RESPONSE.search(text)
    if marker is None:
        return []
    links: list[BriefingLink] = []
    section: str | None = None
    for line in text[marker.end() :].splitlines():
        heading = _HEADING.match(line)
        if heading:
            section = _text(heading.group(1), MAX_SECTION) or None
            continue
        for link_text, raw in _line_links(line):
            url = clean_url(raw)
            if url is not None:
                title = _title(link_text, line, url)
                links.append(BriefingLink(url, title, section, briefing_date, file))
    return links


def briefing_date(path: Path) -> date:
    """The date in the file name (2026-11-10-news.md), else the file's modified date."""
    match = _FILE_DATE.search(path.name)
    if match:
        try:
            return date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def parse_folder(folder: Path) -> list[BriefingLink]:
    """Links from every regular .md file in the folder (symlinks and huge files are skipped)."""
    if not folder.is_dir():
        return []
    links: list[BriefingLink] = []
    for path in sorted(folder.glob("*.md")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        links.extend(parse_briefing(text, briefing_date(path), path.name))
    return links
