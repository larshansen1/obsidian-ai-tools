"""A reading list from the daily briefings (#197).

One-way flow: this machine pulls the briefing files from the server over SSH (rsync, with the
user's own SSH setup), reads the links out of them (briefing_parse.py), drops what the vault
already has, checks the rest exist online (C7), and lists them. Nothing from the vault is sent to
the server, and nothing enters the vault without a click: Ingest goes through the send-to-kai job
(W3, kai_ingest.py).

State lives in compass.duckdb, never in kai's database (ADR 0006):
`briefing_links` (one row per page, with how many briefings mentioned it) and `briefing_runs`
(when the last pull ran). Re-reading the same files changes nothing: counts come from the files,
and a dismissed link stays dismissed.
"""

import hashlib
import re
import socket
import subprocess  # nosec B404
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb
from pydantic import BaseModel

from .briefing_parse import BriefingLink, blocked_host, parse_folder
from .coverage_gaps import _key, ingested_keys, url_exists
from .db import readonly, writable
from .topics import TopicsFile

PULL_TIMEOUT_S = 600
# A pull on `compass serve` start runs only when the last one is older than this.
PULL_EVERY = timedelta(days=1)
# Links that failed the online check are tried again after this long.
RECHECK_AFTER = timedelta(days=7)
# Most links checked in one run, newest first. The rest wait for the next run.
MAX_CHECKS = 500
CHECK_WORKERS = 8

OFF_MESSAGE = (
    "Briefings are off. Set COMPASS_BRIEFINGS_REMOTE (for example user@host:/path/to/briefings/) "
    "in your .env to turn them on."
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS briefing_links (
        id VARCHAR PRIMARY KEY,
        key VARCHAR NOT NULL,
        url VARCHAR NOT NULL,
        title VARCHAR NOT NULL,
        section VARCHAR,
        first_seen DATE NOT NULL,
        last_seen DATE NOT NULL,
        times_seen INTEGER NOT NULL,
        status VARCHAR NOT NULL,
        reachable BOOLEAN,
        checked_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS briefing_runs (
        run_at TIMESTAMP NOT NULL,
        fetched BOOLEAN NOT NULL,
        files INTEGER NOT NULL,
        links INTEGER NOT NULL,
        message VARCHAR NOT NULL
    )
    """,
)

_UPSERT = """
INSERT INTO briefing_links (id, key, url, title, section, first_seen, last_seen, times_seen,
                            status, reachable, checked_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL, NULL)
ON CONFLICT (id) DO UPDATE SET
    url = excluded.url, title = excluded.title, section = excluded.section,
    first_seen = excluded.first_seen, last_seen = excluded.last_seen,
    times_seen = excluded.times_seen
"""
_DONE_URLS_SQL = "SELECT url FROM ingest_jobs WHERE status = 'done'"
# Links from the list that kai added to the vault, newest first, one per link.
MAX_ADDED = 100
_ADDED_SQL = (
    "SELECT b.id, b.title, j.message, j.note_path, j.finished_at "
    "FROM briefing_links b JOIN ingest_jobs j ON j.url = b.url WHERE j.status = 'done' "
    "QUALIFY ROW_NUMBER() OVER (PARTITION BY b.id ORDER BY j.finished_at DESC) = 1 "
    "ORDER BY j.finished_at DESC, b.id LIMIT ?"
)


class BriefingsError(Exception):
    """The pull failed. The message is user-facing."""


# --- Pull -------------------------------------------------------------------------------------


def _source(remote: str) -> str:
    """The rsync source with one trailing slash: copy the folder's files, not the folder itself."""
    if remote.endswith(":/"):
        return remote  # the root of a restricted login (rrsync), already right
    trimmed = remote.rstrip("/")
    return trimmed if trimmed.endswith(":") else trimmed + "/"


def fetch(
    remote: str,
    folder: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Copy new briefing .md files from `remote` (rsync syntax, e.g. user@host:/dir/) to `folder`.

    Read-only toward the server: rsync only reads there. Symlinks and files over 1 MB are not
    copied, and ssh never prompts (BatchMode), so a missing key fails fast instead of hanging.
    `runner` is a test seam for subprocess.run.
    """
    remote = remote.strip()
    if not remote or remote.startswith("-"):
        raise BriefingsError("COMPASS_BRIEFINGS_REMOTE must look like user@host:/path/to/folder/.")
    folder.mkdir(parents=True, exist_ok=True)
    command = [
        "rsync",
        "-rt",
        "--no-links",
        "--max-size=1m",
        "--timeout=60",
        "--include=*.md",
        "--exclude=*",
        "-e",
        "ssh -o BatchMode=yes -o ConnectTimeout=15",
        "--",
        _source(remote),
        str(folder) + "/",
    ]
    try:
        result = runner(command, capture_output=True, text=True, timeout=PULL_TIMEOUT_S)
    except FileNotFoundError:
        raise BriefingsError(
            "rsync is not installed. Install it, then run the pull again."
        ) from None
    except subprocess.TimeoutExpired:
        raise BriefingsError("The pull took more than 10 minutes and was stopped.") from None
    if result.returncode != 0:
        lines = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", result.stderr or "").strip().splitlines()
        detail = lines[-1] if lines else "no message"
        raise BriefingsError(f"rsync failed (exit {result.returncode}): {detail}")


# --- Reachability -----------------------------------------------------------------------------


def resolves_public(
    host: str,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> bool:
    """True when every address the name resolves to is public.

    A public-looking name can point at a private address (DNS rebinding), and Compass makes
    requests to these links, so the answer is checked too. A name that does not resolve is False.
    """
    try:
        infos = resolver(host, None)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and not any(blocked_host(address) for address in addresses)


def reachable(url: str) -> bool:
    """True when the link points to a public host and the page exists online (C7)."""
    host = urlparse(url).hostname or ""
    return resolves_public(host) and url_exists(url)


# --- Refresh ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RefreshReport:
    files: int
    links: int
    checked: int
    unreachable: int
    # Links still waiting for the online check because of the per-run cap.
    left: int


@dataclass(frozen=True)
class _Merged:
    url: str
    title: str
    section: str | None
    first_seen: date
    last_seen: date
    times_seen: int


def link_id(url: str) -> str:
    return hashlib.sha256(_key(url).encode()).hexdigest()[:16]


def merge(links: list[BriefingLink]) -> dict[str, _Merged]:
    """One entry per page. The count is distinct briefings; the newest mention names it."""
    groups: dict[str, list[BriefingLink]] = defaultdict(list)
    for link in links:
        groups[link_id(link.url)].append(link)
    merged = {}
    for key, group in groups.items():
        newest = max(group, key=lambda m: (m.briefing_date, m.file))
        merged[key] = _Merged(
            newest.url,
            newest.title,
            newest.section,
            min(m.briefing_date for m in group),
            newest.briefing_date,
            len({m.file for m in group}),
        )
    return merged


def _done_urls(con: duckdb.DuckDBPyConnection) -> set[str]:
    try:
        return {row[0] for row in con.execute(_DONE_URLS_SQL).fetchall()}
    except duckdb.CatalogException:
        return set()


def _vault_keys(con: duckdb.DuckDBPyConnection) -> set[str]:
    try:
        return ingested_keys(con)
    except duckdb.CatalogException:
        return set()


def _store(con: duckdb.DuckDBPyConnection, merged: dict[str, _Merged]) -> None:
    for link_key, m in merged.items():
        con.execute(
            _UPSERT,
            [
                link_key,
                _key(m.url),
                m.url,
                m.title,
                m.section,
                m.first_seen,
                m.last_seen,
                m.times_seen,
            ],
        )


def _mark_ingested(con: duckdb.DuckDBPyConnection) -> None:
    """Links the vault already has, or that kai finished ingesting, leave the list for good."""
    vault, done = _vault_keys(con), _done_urls(con)
    rows = con.execute("SELECT id, key, url FROM briefing_links WHERE status = 'new'").fetchall()
    for link_key, key, url in rows:
        if key in vault or url in done:
            con.execute("UPDATE briefing_links SET status = 'ingested' WHERE id = ?", [link_key])


def _to_check(con: duckdb.DuckDBPyConnection, now: datetime, cap: int) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in con.execute(
            "SELECT id, url FROM briefing_links WHERE status = 'new' AND "
            "(reachable IS NULL OR (NOT reachable AND checked_at < ?)) "
            "ORDER BY last_seen DESC, times_seen DESC, id LIMIT ?",
            [now - RECHECK_AFTER, cap],
        ).fetchall()
    ]


def refresh_links(
    db_path: Path,
    folder: Path,
    *,
    check: Callable[[str], bool] = reachable,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_checks: int = MAX_CHECKS,
) -> RefreshReport:
    """Read the folder, update the table, then check new links online.

    The online checks make network calls, so they run with no DuckDB connection open.
    """
    now = clock().replace(tzinfo=None)
    links = parse_folder(folder)
    merged = merge(links)
    with writable(db_path) as con:
        for ddl in _DDL:
            con.execute(ddl)
        _store(con, merged)
        _mark_ingested(con)
        pending = _to_check(con, now, max_checks)
        remaining = con.execute(
            "SELECT COUNT(*) FROM briefing_links WHERE status = 'new' AND reachable IS NULL"
        ).fetchone()
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
        answers = list(pool.map(check, [url for _, url in pending]))
    with writable(db_path) as con:
        for (link_key, _), ok in zip(pending, answers, strict=True):
            con.execute(
                "UPDATE briefing_links SET reachable = ?, checked_at = ? WHERE id = ?",
                [ok, now, link_key],
            )
    left = max(0, int(remaining[0] if remaining else 0) - len(pending))
    return RefreshReport(
        files=len({link.file for link in links}),
        links=len(merged),
        checked=len(pending),
        unreachable=answers.count(False),
        left=left,
    )


def record_run(db_path: Path, *, fetched: bool, report: RefreshReport, now: datetime) -> None:
    message = f"{report.files} files, {report.links} links, {report.checked} checked"
    with writable(db_path) as con:
        for ddl in _DDL:
            con.execute(ddl)
        con.execute(
            "INSERT INTO briefing_runs VALUES (?, ?, ?, ?, ?)",
            [now.replace(tzinfo=None), fetched, report.files, report.links, message],
        )


def last_pull(db_path: Path) -> datetime | None:
    """When the last successful pull ran (UTC), or None."""
    if not db_path.exists():
        return None
    with readonly(db_path) as con:
        try:
            row = con.execute("SELECT MAX(run_at) FROM briefing_runs WHERE fetched").fetchone()
        except duckdb.CatalogException:
            return None
    return row[0].replace(tzinfo=UTC) if row and row[0] is not None else None


def pull_due(db_path: Path, now: datetime) -> bool:
    last = last_pull(db_path)
    return last is None or now - last >= PULL_EVERY


def pull_and_refresh(
    remote: str,
    folder: Path,
    db_path: Path,
    *,
    check: Callable[[str], bool] = reachable,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    runner: Callable[..., Any] = subprocess.run,
) -> RefreshReport:
    """Fetch the briefing files, then read and check them. Raises BriefingsError on a bad pull."""
    fetch(remote, folder, runner)
    report = refresh_links(db_path, folder, check=check, clock=clock)
    record_run(db_path, fetched=True, report=report, now=clock())
    return report


# --- The list ---------------------------------------------------------------------------------


class BriefingRow(BaseModel):
    id: str
    url: str
    title: str
    site: str
    # The newest briefing that mentioned it.
    date: date
    section: str | None
    times_seen: int


class BriefingGroup(BaseModel):
    # None is the "no topic" group.
    topic_id: str | None
    topic_name: str | None
    links: list[BriefingRow]


class BriefingAdded(BaseModel):
    """A link from the list that kai added to the vault, kept so a refresh still shows it."""

    id: str
    title: str
    message: str
    note_path: str | None
    finished_at: datetime | None


class BriefingList(BaseModel):
    groups: list[BriefingGroup]
    added: list[BriefingAdded] = []
    # New links not yet checked online: they appear once the check passes.
    pending_checks: int
    last_pull: datetime | None


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def match_topic(title: str, definitions: TopicsFile) -> str | None:
    """The topic with the most tags found as whole words in the title; the first listed wins ties.

    Rough on purpose (#197): no match means the "no topic" group.
    """
    padded = f" {_words(title)} "
    best: str | None = None
    best_score = 0
    for topic_id, topic in definitions.topics.items():
        words = (_words(tag) for tag in topic.tags)
        score = sum(1 for tag in words if tag and f" {tag} " in padded)
        if score > best_score:
            best, best_score = topic_id, score
    return best


def _added(con: duckdb.DuckDBPyConnection) -> list[BriefingAdded]:
    try:
        rows = con.execute(_ADDED_SQL, [MAX_ADDED]).fetchall()
    except duckdb.CatalogException:
        return []
    return [
        BriefingAdded(
            id=link_key,
            title=title,
            message=message,
            note_path=note_path,
            finished_at=done.replace(tzinfo=UTC)
            if done is not None and done.tzinfo is None
            else done,
        )
        for link_key, title, message, note_path, done in rows
    ]


def _site(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def build_list(db_path: Path, definitions: TopicsFile) -> BriefingList:
    """Links that exist online, are not in the vault, not dismissed and not already ingested.

    Newest first inside each topic group; the "no topic" group is last.
    """
    empty = BriefingList(groups=[], pending_checks=0, last_pull=last_pull(db_path))
    if not db_path.exists():
        return empty
    with readonly(db_path) as con:
        try:
            rows = con.execute(
                "SELECT id, key, url, title, section, last_seen, times_seen, reachable "
                "FROM briefing_links WHERE status = 'new' "
                "ORDER BY last_seen DESC, times_seen DESC, title, id"
            ).fetchall()
        except duckdb.CatalogException:
            return empty
        vault, done = _vault_keys(con), _done_urls(con)
        added = _added(con)
    by_topic: dict[str | None, list[BriefingRow]] = defaultdict(list)
    pending = 0
    for link_key, key, url, title, section, seen, times, ok in rows:
        if key in vault or url in done:
            continue
        if ok is None:
            pending += 1
        elif ok:
            by_topic[match_topic(title, definitions)].append(
                BriefingRow(
                    id=link_key,
                    url=url,
                    title=title,
                    site=_site(url),
                    date=seen,
                    section=section,
                    times_seen=times,
                )
            )
    groups = [
        BriefingGroup(topic_id=topic_id, topic_name=topic.name, links=by_topic[topic_id])
        for topic_id, topic in definitions.topics.items()
        if by_topic.get(topic_id)
    ]
    if by_topic.get(None):
        groups.append(BriefingGroup(topic_id=None, topic_name=None, links=by_topic[None]))
    return BriefingList(
        groups=groups, added=added, pending_checks=pending, last_pull=empty.last_pull
    )


def dismiss(db_path: Path, link_id_: str) -> bool:
    """Take a link off the list for good. False when there is no such link."""
    if not db_path.exists():
        return False
    with writable(db_path) as con:
        try:
            found = con.execute("SELECT 1 FROM briefing_links WHERE id = ?", [link_id_]).fetchone()
        except duckdb.CatalogException:
            return False
        if found is None:
            return False
        con.execute("UPDATE briefing_links SET status = 'dismissed' WHERE id = ?", [link_id_])
    return True
