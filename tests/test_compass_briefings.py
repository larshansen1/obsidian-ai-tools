"""The briefings reading list: pull, store, check, list, dismiss, API and CLI (#197)."""

import logging
import socket
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from vault_compass import cli as compass_cli
from vault_compass.app import create_app
from vault_compass.briefing_parse import BriefingLink
from vault_compass.briefings import (
    MAX_ADDED,
    MAX_CHECKS,
    OFF_MESSAGE,
    PULL_EVERY,
    PULL_TIMEOUT_S,
    RECHECK_AFTER,
    BriefingAdded,
    BriefingGroup,
    BriefingList,
    BriefingRow,
    BriefingsError,
    RefreshReport,
    build_list,
    dismiss,
    fetch,
    last_pull,
    link_id,
    match_topic,
    merge,
    pull_and_refresh,
    pull_due,
    reachable,
    record_run,
    refresh_links,
    resolves_public,
)
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.topics import TopicsFile, load_topics, parse_topics

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
TOPICS_YAML = """\
topics:
  sleep:
    name: Sleep
    tags: [sleep, circadian-rhythm]
  ai:
    name: AI
    tags: [ai, claude-code, llm]
min_notes: 1
trend_start: 2026-04-01
ai_exclude_folders: []
"""
# The columns of kai's ingest_jobs table that the list reads.
INGEST_JOBS_DDL = (
    "CREATE TABLE ingest_jobs (url VARCHAR, status VARCHAR, message VARCHAR, "
    "note_path VARCHAR, finished_at TIMESTAMP)"
)
RESPONSE = "# Briefing\n\nPrompt link: https://prompt.example.org/x\n\n## Response\n\n"


def _topics() -> TopicsFile:
    return parse_topics(TOPICS_YAML)


def _write(folder: Path, name: str, *lines: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(RESPONSE + "\n".join(lines) + "\n", encoding="utf-8")


def _link(url: str, d: date, file: str, title: str = "T", section: str | None = None):
    return BriefingLink(url, title, section, d, file)


class Checker:
    """A fake online check: records every URL asked about."""

    def __init__(self, answers: dict[str, bool] | None = None, default: bool = True) -> None:
        self.answers = answers or {}
        self.default = default
        self.asked: list[str] = []

    def __call__(self, url: str) -> bool:
        self.asked.append(url)
        return self.answers.get(url, self.default)


def _refresh(
    db: Path, folder: Path, check: Checker, now: datetime = NOW, **kwargs: Any
) -> RefreshReport:
    return refresh_links(db, folder, check=check, clock=lambda: now, **kwargs)


def _rows(db: Path, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(db))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.fixture
def paths(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(folder=tmp_path / "briefings", db=tmp_path / "compass.duckdb")


# ---------------------------------------------------------------------------
# Merging mentions
# ---------------------------------------------------------------------------


def test_the_same_page_in_three_briefings_is_one_link_with_a_count() -> None:
    links = [
        _link("https://example.com/a", date(2026, 11, 2), "b.md", "Mid", "S2"),
        _link("https://example.com/a", date(2026, 11, 1), "a.md", "Old", "S1"),
        _link("https://EXAMPLE.com/a/", date(2026, 11, 3), "c.md", "New", "S3"),
    ]

    merged = merge(links)

    assert list(merged) == [link_id("https://example.com/a")]
    only = merged[link_id("https://example.com/a")]
    assert (only.url, only.title, only.section) == ("https://EXAMPLE.com/a/", "New", "S3")
    assert (only.first_seen, only.last_seen, only.times_seen) == (
        date(2026, 11, 1),
        date(2026, 11, 3),
        3,
    )


def test_two_mentions_in_one_briefing_count_once() -> None:
    links = [_link("https://example.com/a", date(2026, 11, 1), "a.md")] * 2

    assert merge(links)[link_id("https://example.com/a")].times_seen == 1


def test_on_the_same_day_the_later_file_names_the_link() -> None:
    d = date(2026, 11, 1)
    links = [
        _link("https://example.com/a", d, "a.md", "From a"),
        _link("https://example.com/a", d, "b.md", "From b"),
    ]

    assert merge(links)[link_id("https://example.com/a")].title == "From b"


def test_a_different_query_is_a_different_page() -> None:
    assert link_id("https://example.com/a?id=1") != link_id("https://example.com/a?id=2")


# ---------------------------------------------------------------------------
# Refresh and the list
# ---------------------------------------------------------------------------


def test_the_list_is_filled_from_the_files(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "## AI", "[Claude Code ships](https://example.com/a)")
    _write(paths.folder, "2026-11-02.md", "[Deep sleep study](https://example.com/b)")
    check = Checker()

    report = _refresh(paths.db, paths.folder, check)

    assert report == RefreshReport(files=2, links=2, checked=2, unreachable=0, left=0)
    assert sorted(check.asked) == ["https://example.com/a", "https://example.com/b"]
    assert build_list(paths.db, _topics()) == BriefingList(
        groups=[
            BriefingGroup(
                topic_id="sleep",
                topic_name="Sleep",
                links=[
                    BriefingRow(
                        id=link_id("https://example.com/b"),
                        url="https://example.com/b",
                        title="Deep sleep study",
                        site="example.com",
                        date=date(2026, 11, 2),
                        section=None,
                        times_seen=1,
                    )
                ],
            ),
            BriefingGroup(
                topic_id="ai",
                topic_name="AI",
                links=[
                    BriefingRow(
                        id=link_id("https://example.com/a"),
                        url="https://example.com/a",
                        title="Claude Code ships",
                        site="example.com",
                        date=date(2026, 11, 1),
                        section="AI",
                        times_seen=1,
                    )
                ],
            ),
        ],
        pending_checks=0,
        last_pull=None,
    )


def test_prompt_links_and_google_news_links_never_get_stored(paths: SimpleNamespace) -> None:
    _write(
        paths.folder,
        "2026-11-01.md",
        "[Redirect](https://news.google.com/rss/articles/CBMi1?oc=5)",
        "[Real](https://example.com/real)",
    )
    check = Checker()

    _refresh(paths.db, paths.folder, check)

    assert check.asked == ["https://example.com/real"]
    assert [r[0] for r in _rows(paths.db, "SELECT url FROM briefing_links")] == [
        "https://example.com/real"
    ]


def test_a_link_in_several_briefings_is_listed_once_with_the_right_count(
    paths: SimpleNamespace,
) -> None:
    for day in ("01", "02", "03"):
        _write(
            paths.folder, f"2026-11-{day}.md", "[Sleep news](https://example.com/a?utm_source=x)"
        )
    check = Checker()

    _refresh(paths.db, paths.folder, check)

    rows = _rows(paths.db, "SELECT url, first_seen, last_seen, times_seen FROM briefing_links")
    assert rows == [("https://example.com/a", date(2026, 11, 1), date(2026, 11, 3), 3)]
    assert check.asked == ["https://example.com/a"]


def test_reading_the_same_files_again_changes_nothing(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[Sleep news](https://example.com/a)")
    check = Checker()
    _refresh(paths.db, paths.folder, check)
    first = _rows(paths.db, "SELECT * FROM briefing_links")

    second_report = _refresh(paths.db, paths.folder, check, NOW + timedelta(hours=1))

    assert _rows(paths.db, "SELECT * FROM briefing_links") == first
    assert check.asked == ["https://example.com/a"]
    assert second_report == RefreshReport(files=1, links=1, checked=0, unreachable=0, left=0)


def test_a_new_briefing_raises_the_count_of_a_known_link(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[Sleep news](https://example.com/a)")
    _refresh(paths.db, paths.folder, Checker())

    _write(paths.folder, "2026-11-04.md", "[Sleep news](https://example.com/a)")
    _refresh(paths.db, paths.folder, Checker())

    rows = _rows(paths.db, "SELECT first_seen, last_seen, times_seen FROM briefing_links")
    assert rows == [(date(2026, 11, 1), date(2026, 11, 4), 2)]


def test_a_link_that_does_not_open_is_not_shown(paths: SimpleNamespace) -> None:
    _write(
        paths.folder,
        "2026-11-01.md",
        "[Gone](https://example.com/gone)",
        "[Ok](https://example.com/ok)",
    )
    check = Checker({"https://example.com/gone": False})

    report = _refresh(paths.db, paths.folder, check)

    assert (report.checked, report.unreachable) == (2, 1)
    shown = [r.url for g in build_list(paths.db, _topics()).groups for r in g.links]
    assert shown == ["https://example.com/ok"]


def test_a_link_not_yet_checked_is_counted_not_shown(paths: SimpleNamespace) -> None:
    _write(
        paths.folder,
        "2026-11-01.md",
        "[One](https://example.com/1)",
        "[Two](https://example.com/2)",
        "[Three](https://example.com/3)",
    )
    _write(paths.folder, "2026-11-02.md", "[Four](https://example.com/4)")

    check = Checker()

    report = _refresh(paths.db, paths.folder, check, max_checks=3)

    assert report == RefreshReport(files=2, links=4, checked=3, unreachable=0, left=1)
    # The newest briefing is checked first.
    assert check.asked[0] == "https://example.com/4"
    assert len(check.asked) == 3
    listing = build_list(paths.db, _topics())
    assert listing.pending_checks == 1
    shown = sorted(r.url for g in listing.groups for r in g.links)
    assert shown == sorted(check.asked)


def test_the_check_cap_default_is_500() -> None:
    assert MAX_CHECKS == 500


def test_a_failed_check_is_repeated_only_after_seven_days(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[Flaky](https://example.com/a)")
    check = Checker(default=False)
    _refresh(paths.db, paths.folder, check, NOW)

    _refresh(paths.db, paths.folder, check, NOW + RECHECK_AFTER)
    assert len(check.asked) == 1

    check.default = True
    _refresh(paths.db, paths.folder, check, NOW + RECHECK_AFTER + timedelta(seconds=1))

    assert len(check.asked) == 2
    assert [r.url for g in build_list(paths.db, _topics()).groups for r in g.links] == [
        "https://example.com/a"
    ]


def test_a_passed_check_is_not_repeated(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[Fine](https://example.com/a)")
    check = Checker()
    _refresh(paths.db, paths.folder, check, NOW)

    _refresh(paths.db, paths.folder, check, NOW + timedelta(days=60))

    assert check.asked == ["https://example.com/a"]


def test_a_dismissed_link_stays_dismissed_after_the_next_pull(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[Sleep news](https://example.com/a)")
    _refresh(paths.db, paths.folder, Checker())
    assert dismiss(paths.db, link_id("https://example.com/a")) is True
    assert build_list(paths.db, _topics()).groups == []

    _write(paths.folder, "2026-11-05.md", "[Sleep news](https://example.com/a)")
    check = Checker()
    _refresh(paths.db, paths.folder, check, NOW + timedelta(days=1))

    assert build_list(paths.db, _topics()).groups == []
    assert check.asked == []
    assert _rows(paths.db, "SELECT status, times_seen FROM briefing_links") == [("dismissed", 2)]


def test_dismiss_of_an_unknown_link_or_database_is_false(paths: SimpleNamespace) -> None:
    assert dismiss(paths.db, "nope") is False
    duckdb.connect(str(paths.db)).close()
    assert dismiss(paths.db, "nope") is False
    _write(paths.folder, "2026-11-01.md", "[A](https://example.com/a)")
    _refresh(paths.db, paths.folder, Checker())
    assert dismiss(paths.db, "nope") is False
    assert _rows(paths.db, "SELECT status FROM briefing_links") == [("new",)]


def _vault_with_source(tmp_path: Path, source_url: str) -> Path:
    root = tmp_path / "vault"
    (root / ".kai").mkdir(parents=True)
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "have.md").write_text(
        f"---\ntitle: Have\ntags: [sleep]\nsource_url: {source_url}\n---\nBody.\n",
        encoding="utf-8",
    )
    return root


def test_a_link_already_in_the_vault_is_not_listed(tmp_path: Path, paths: SimpleNamespace) -> None:
    vault = _vault_with_source(tmp_path, "https://Example.com/a/")
    refresh_notes(vault, paths.db, load_topics(vault / ".kai" / "topics.yaml"))
    _write(
        paths.folder,
        "2026-11-01.md",
        "[Have](https://example.com/a)",
        "[New](https://example.com/b)",
    )
    check = Checker()

    _refresh(paths.db, paths.folder, check)

    assert check.asked == ["https://example.com/b"]
    shown = [r.url for g in build_list(paths.db, _topics()).groups for r in g.links]
    assert shown == ["https://example.com/b"]
    assert _rows(paths.db, "SELECT url, status FROM briefing_links ORDER BY url") == [
        ("https://example.com/a", "ingested"),
        ("https://example.com/b", "new"),
    ]


def test_a_note_ingested_after_the_last_pull_leaves_the_list(
    tmp_path: Path, paths: SimpleNamespace
) -> None:
    _write(paths.folder, "2026-11-01.md", "[Have](https://example.com/a)")
    _refresh(paths.db, paths.folder, Checker())
    assert len(build_list(paths.db, _topics()).groups) == 1

    vault = _vault_with_source(tmp_path, "https://example.com/a")
    refresh_notes(vault, paths.db, load_topics(vault / ".kai" / "topics.yaml"))

    assert build_list(paths.db, _topics()).groups == []


def test_a_link_kai_finished_ingesting_leaves_the_list(paths: SimpleNamespace) -> None:
    _write(
        paths.folder, "2026-11-01.md", "[A](https://example.com/a)", "[B](https://example.com/b)"
    )
    _refresh(paths.db, paths.folder, Checker())
    con = duckdb.connect(str(paths.db))
    con.execute(INGEST_JOBS_DDL)
    con.execute(
        "INSERT INTO ingest_jobs (url, status, message) VALUES "
        "('https://example.com/a', 'done', 'Added'), ('https://example.com/b', 'failed', 'No')"
    )
    con.close()

    shown = [r.url for g in build_list(paths.db, _topics()).groups for r in g.links]
    assert shown == ["https://example.com/b"]

    _refresh(paths.db, paths.folder, Checker())
    assert _rows(paths.db, "SELECT url, status FROM briefing_links ORDER BY url") == [
        ("https://example.com/a", "ingested"),
        ("https://example.com/b", "new"),
    ]


def test_links_kai_added_stay_in_the_list_response_with_their_note(
    paths: SimpleNamespace,
) -> None:
    _write(
        paths.folder,
        "2026-11-01.md",
        "[Older](https://example.com/a)",
        "[Newer](https://example.com/b)",
        "[Never](https://example.com/c)",
    )
    _refresh(paths.db, paths.folder, Checker())
    con = duckdb.connect(str(paths.db))
    con.execute(INGEST_JOBS_DDL)
    con.execute(
        "INSERT INTO ingest_jobs VALUES "
        "('https://example.com/a', 'done', 'Added A', 'inbox/a.md', '2026-11-10 10:00:00'), "
        "('https://example.com/a', 'done', 'Added A again', 'inbox/a2.md', '2026-11-10 11:00:00'), "
        "('https://example.com/b', 'done', 'Added B', NULL, '2026-11-10 12:00:00'), "
        "('https://example.com/c', 'failed', 'Could not read it', NULL, '2026-11-10 13:00:00'), "
        "('https://example.com/other', 'done', 'Not from a briefing', 'x.md', "
        "'2026-11-10 14:00:00')"
    )
    con.close()

    added = build_list(paths.db, _topics()).added

    assert added == [
        BriefingAdded(
            id=link_id("https://example.com/b"),
            title="Newer",
            message="Added B",
            note_path=None,
            finished_at=datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC),
        ),
        BriefingAdded(
            id=link_id("https://example.com/a"),
            title="Older",
            message="Added A again",
            note_path="inbox/a2.md",
            finished_at=datetime(2026, 11, 10, 11, 0, 0, tzinfo=UTC),
        ),
    ]


def test_no_links_were_added_without_an_ingest_table(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[A](https://example.com/a)")
    _refresh(paths.db, paths.folder, Checker())

    assert build_list(paths.db, _topics()).added == []


def test_the_added_list_is_cut_at_its_limit(paths: SimpleNamespace) -> None:
    lines = [f"[L{i}](https://example.com/{i})" for i in range(MAX_ADDED + 2)]
    _write(paths.folder, "2026-11-01.md", *lines)
    _refresh(paths.db, paths.folder, Checker())
    con = duckdb.connect(str(paths.db))
    con.execute(INGEST_JOBS_DDL)
    for i in range(MAX_ADDED + 2):
        con.execute(
            "INSERT INTO ingest_jobs VALUES (?, 'done', 'Added', NULL, '2026-11-10 10:00:00')",
            [f"https://example.com/{i}"],
        )
    con.close()

    assert len(build_list(paths.db, _topics()).added) == MAX_ADDED


def test_the_list_is_empty_without_a_database_or_table(paths: SimpleNamespace) -> None:
    empty = BriefingList(groups=[], pending_checks=0, last_pull=None)
    assert build_list(paths.db, _topics()) == empty

    duckdb.connect(str(paths.db)).close()
    assert build_list(paths.db, _topics()) == empty


def test_groups_follow_topic_order_newest_first_with_no_topic_last(
    paths: SimpleNamespace,
) -> None:
    _write(
        paths.folder,
        "2026-11-01.md",
        "[Old llm piece](https://example.com/old)",
        "[Misc thing](https://example.com/misc)",
    )
    _write(
        paths.folder,
        "2026-11-03.md",
        "[New llm piece](https://example.com/new)",
        "[Sleep matters](https://example.com/sleep)",
        "[Old llm piece](https://example.com/old)",
    )
    _write(paths.folder, "2026-11-02.md", "[Mid llm piece](https://example.com/mid)")

    _refresh(paths.db, paths.folder, Checker())
    groups = build_list(paths.db, _topics()).groups

    assert [(g.topic_id, g.topic_name) for g in groups] == [
        ("sleep", "Sleep"),
        ("ai", "AI"),
        (None, None),
    ]
    assert [[r.title for r in g.links] for g in groups] == [
        ["Sleep matters"],
        # Newest first; the 3 Nov tie is broken by how many briefings mentioned it.
        ["Old llm piece", "New llm piece", "Mid llm piece"],
        ["Misc thing"],
    ]
    assert [r.times_seen for r in groups[1].links] == [2, 1, 1]


def test_the_site_drops_www_and_the_case(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[Misc](https://WWW.Example.com/a)")

    _refresh(paths.db, paths.folder, Checker())

    assert build_list(paths.db, _topics()).groups[0].links[0].site == "example.com"


def test_a_title_with_markup_comes_back_as_plain_text(paths: SimpleNamespace) -> None:
    _write(
        paths.folder,
        "2026-11-01.md",
        "[**Big** <script>alert(1)</script> [x]](https://example.com/a)",
    )

    _refresh(paths.db, paths.folder, Checker())

    title = build_list(paths.db, _topics()).groups[0].links[0].title
    assert title == "Big <script>alert(1)</script> [x]"


# ---------------------------------------------------------------------------
# Topic matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "topic"),
    [
        ("Sleep is good", "sleep"),
        ("Why SLEEP matters", "sleep"),
        ("Circadian rhythm reset", "sleep"),
        ("circadian-rhythm study", "sleep"),
        ("Claude Code review", "ai"),
        ("An LLM story", "ai"),
        ("Said the train", None),
        ("Sleeping late", None),
        ("Nothing here", None),
        ("", None),
    ],
)
def test_a_title_matches_a_topic_by_whole_tag_words(title: str, topic: str | None) -> None:
    assert match_topic(title, _topics()) == topic


def test_the_topic_with_more_matching_tags_wins_and_ties_go_to_the_first() -> None:
    assert match_topic("sleep and the llm", _topics()) == "sleep"
    assert match_topic("sleep, llm and claude code", _topics()) == "ai"


# ---------------------------------------------------------------------------
# Pulling
# ---------------------------------------------------------------------------


class Runner:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result or SimpleNamespace(returncode=0, stderr="")
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> Any:
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        return self.result


def test_fetch_runs_rsync_read_only_toward_the_server(tmp_path: Path) -> None:
    runner = Runner()
    folder = tmp_path / "deep" / "briefings"

    fetch("user@host:/srv/briefings", folder, runner)

    assert folder.is_dir()
    assert runner.calls == [
        (
            [
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
                "user@host:/srv/briefings/",
                f"{folder}/",
            ],
            {"capture_output": True, "text": True, "timeout": PULL_TIMEOUT_S},
        )
    ]


def test_fetch_keeps_one_trailing_slash_and_trims_spaces(tmp_path: Path) -> None:
    runner = Runner()

    fetch("  host:/srv/b//  ", tmp_path, runner)

    assert runner.calls[0][0][-2] == "host:/srv/b/"


@pytest.mark.parametrize(
    ("remote", "source"),
    [
        ("h:/srv/b", "h:/srv/b/"),
        ("h:/srv/b/", "h:/srv/b/"),
        ("u@h:rel/dir", "u@h:rel/dir/"),
        ("u@h:", "u@h:"),
        ("u@h:/", "u@h:/"),
    ],
)
def test_fetch_copies_the_files_inside_the_remote_folder(
    tmp_path: Path, remote: str, source: str
) -> None:
    runner = Runner()

    fetch(remote, tmp_path, runner)

    assert runner.calls[0][0][-2] == source


def test_the_pull_timeout_is_ten_minutes() -> None:
    assert PULL_TIMEOUT_S == 600


@pytest.mark.parametrize("remote", ["", "   ", "-e sh", "--rsh=evil"])
def test_fetch_refuses_an_empty_remote_or_one_that_looks_like_an_option(
    tmp_path: Path, remote: str
) -> None:
    runner = Runner()

    with pytest.raises(BriefingsError) as raised:
        fetch(remote, tmp_path, runner)

    assert str(raised.value) == (
        "COMPASS_BRIEFINGS_REMOTE must look like user@host:/path/to/folder/."
    )
    assert runner.calls == []


def test_fetch_says_when_rsync_is_missing(tmp_path: Path) -> None:
    with pytest.raises(BriefingsError) as raised:
        fetch("h:/b", tmp_path, Runner(error=FileNotFoundError()))

    assert str(raised.value) == "rsync is not installed. Install it, then run the pull again."


def test_fetch_says_when_the_pull_times_out(tmp_path: Path) -> None:
    error = subprocess.TimeoutExpired("rsync", PULL_TIMEOUT_S)

    with pytest.raises(BriefingsError) as raised:
        fetch("h:/b", tmp_path, Runner(error=error))

    assert str(raised.value) == "The pull took more than 10 minutes and was stopped."


def test_fetch_reports_the_last_line_of_the_error_without_control_characters(
    tmp_path: Path,
) -> None:
    result = SimpleNamespace(
        returncode=23, stderr="first\nPermission denied\x1b\x07 (publickey).\n"
    )

    with pytest.raises(BriefingsError) as raised:
        fetch("h:/b", tmp_path, Runner(result))

    assert str(raised.value) == "rsync failed (exit 23): Permission denied   (publickey)."


def test_fetch_reports_a_failure_with_no_message(tmp_path: Path) -> None:
    with pytest.raises(BriefingsError) as raised:
        fetch("h:/b", tmp_path, Runner(SimpleNamespace(returncode=1, stderr=None)))

    assert str(raised.value) == "rsync failed (exit 1): no message"


def test_pull_and_refresh_fetches_reads_and_logs_the_run(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[A](https://example.com/a)")
    runner = Runner()
    check = Checker()

    report = pull_and_refresh(
        "h:/b", paths.folder, paths.db, check=check, clock=lambda: NOW, runner=runner
    )

    assert report == RefreshReport(files=1, links=1, checked=1, unreachable=0, left=0)
    assert len(runner.calls) == 1
    assert _rows(paths.db, "SELECT run_at, fetched, files, links, message FROM briefing_runs") == [
        (NOW.replace(tzinfo=None), True, 1, 1, "1 files, 1 links, 1 checked")
    ]
    assert last_pull(paths.db) == NOW


def test_a_failed_pull_reads_nothing_and_logs_nothing(paths: SimpleNamespace) -> None:
    _write(paths.folder, "2026-11-01.md", "[A](https://example.com/a)")
    runner = Runner(SimpleNamespace(returncode=255, stderr="no route"))

    with pytest.raises(BriefingsError):
        pull_and_refresh("h:/b", paths.folder, paths.db, check=Checker(), runner=runner)

    assert not paths.db.exists()


def test_the_last_pull_ignores_runs_that_only_read_local_files(paths: SimpleNamespace) -> None:
    assert last_pull(paths.db) is None
    duckdb.connect(str(paths.db)).close()
    assert last_pull(paths.db) is None
    report = RefreshReport(0, 0, 0, 0, 0)
    record_run(paths.db, fetched=False, report=report, now=NOW)
    assert last_pull(paths.db) is None
    record_run(paths.db, fetched=True, report=report, now=NOW - timedelta(days=2))
    record_run(paths.db, fetched=False, report=report, now=NOW)

    assert last_pull(paths.db) == NOW - timedelta(days=2)


def test_a_pull_is_due_after_one_day(paths: SimpleNamespace) -> None:
    assert pull_due(paths.db, NOW) is True
    report = RefreshReport(0, 0, 0, 0, 0)
    record_run(paths.db, fetched=True, report=report, now=NOW)

    assert PULL_EVERY == timedelta(days=1)
    assert pull_due(paths.db, NOW + PULL_EVERY - timedelta(seconds=1)) is False
    assert pull_due(paths.db, NOW + PULL_EVERY) is True


# ---------------------------------------------------------------------------
# The online check
# ---------------------------------------------------------------------------


def _answer(*addresses: str) -> Callable[..., list[Any]]:
    def resolver(host: str, port: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]

    return resolver


@pytest.mark.parametrize(
    ("addresses", "expected"),
    [
        (("93.184.216.34",), True),
        (("93.184.216.34", "2606:2800:220:1::1"), True),
        (("127.0.0.1",), False),
        (("93.184.216.34", "10.0.0.5"), False),
        (("169.254.169.254",), False),
        ((), False),
    ],
)
def test_a_name_must_resolve_only_to_public_addresses(
    addresses: tuple[str, ...], expected: bool
) -> None:
    assert resolves_public("example.com", _answer(*addresses)) is expected


def test_a_name_that_does_not_resolve_is_not_public() -> None:
    def resolver(host: str, port: Any) -> list[Any]:
        raise socket.gaierror("no such host")

    assert resolves_public("nope.example", resolver) is False


def test_resolution_asks_for_the_host_with_no_port() -> None:
    resolver = MagicMock(return_value=[(socket.AF_INET, 1, 6, "", ("93.184.216.34", 0))])

    resolves_public("example.com", resolver)

    resolver.assert_called_with("example.com", None)


def test_reachable_asks_the_web_only_for_a_public_host() -> None:
    with (
        patch("vault_compass.briefings.resolves_public", return_value=True) as resolve,
        patch("vault_compass.briefings.url_exists", return_value=True) as exists,
    ):
        assert reachable("https://example.com/a") is True

    resolve.assert_called_with("example.com")
    exists.assert_called_with("https://example.com/a")


def test_reachable_never_requests_a_host_that_resolves_to_a_private_address() -> None:
    with (
        patch("vault_compass.briefings.resolves_public", return_value=False),
        patch("vault_compass.briefings.url_exists") as exists,
    ):
        assert reachable("https://sneaky.example/a") is False

    exists.assert_not_called()


def test_reachable_follows_the_page_check() -> None:
    with (
        patch("vault_compass.briefings.resolves_public", return_value=True),
        patch("vault_compass.briefings.url_exists", return_value=False),
    ):
        assert reachable("https://example.com/a") is False


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "inbox").mkdir(parents=True)
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    refresh_notes(root, root / ".kai" / "compass.duckdb", load_topics(root / ".kai/topics.yaml"))
    return root


def _settings(vault: Path, folder: Path, remote: str = "") -> CompassSettings:
    return CompassSettings(
        obsidian_vault_path=vault,
        openrouter_api_key=None,
        compass_briefings_remote=remote,
        compass_briefings_dir=folder,
    )


def _api(settings: CompassSettings, **kwargs: Any) -> TestClient:
    return TestClient(create_app(settings, **kwargs))


def test_api_lists_the_reading_list_and_dismiss_removes_a_row(vault: Path, tmp_path: Path) -> None:
    folder = tmp_path / "briefings"
    settings = _settings(vault, folder)
    _write(folder, "2026-11-01.md", "[Sleep news](https://example.com/a)")
    refresh_links(settings.compass_db_path, folder, check=Checker(), clock=lambda: NOW)
    api = _api(settings)

    listed = api.get("/briefings")

    assert listed.status_code == 200
    body = listed.json()
    assert body["pending_checks"] == 0
    assert body["last_pull"] is None
    assert body["groups"] == [
        {
            "topic_id": "sleep",
            "topic_name": "Sleep",
            "links": [
                {
                    "id": link_id("https://example.com/a"),
                    "url": "https://example.com/a",
                    "title": "Sleep news",
                    "site": "example.com",
                    "date": "2026-11-01",
                    "section": None,
                    "times_seen": 1,
                }
            ],
        }
    ]

    gone = api.post(f"/briefings/{link_id('https://example.com/a')}/dismiss")

    assert (gone.status_code, gone.content) == (204, b"")
    assert api.get("/briefings").json()["groups"] == []


def test_api_dismiss_of_an_unknown_link_is_404(vault: Path, tmp_path: Path) -> None:
    api = _api(_settings(vault, tmp_path / "briefings"))

    response = api.post("/briefings/nope/dismiss")

    assert (response.status_code, response.json()) == (404, {"detail": "No such link."})


def test_api_list_is_empty_before_any_pull(vault: Path, tmp_path: Path) -> None:
    api = _api(_settings(vault, tmp_path / "briefings"))

    assert api.get("/briefings").json() == {
        "groups": [],
        "added": [],
        "pending_checks": 0,
        "last_pull": None,
    }


def test_api_list_reports_a_broken_topics_file(vault: Path, tmp_path: Path) -> None:
    (vault / ".kai" / "topics.yaml").write_text("topics: [", encoding="utf-8")
    api = _api(_settings(vault, tmp_path / "briefings"))

    response = api.get("/briefings")

    assert response.status_code == 500
    assert response.json()["detail"].startswith("topics file is not valid YAML")


def test_serve_start_pulls_in_the_background_when_a_remote_is_set(
    vault: Path, tmp_path: Path
) -> None:
    spawned: list[Callable[[], None]] = []
    settings = _settings(vault, tmp_path / "briefings", remote="h:/b")

    with TestClient(create_app(settings, spawn=spawned.append)):
        pass

    assert len(spawned) == 1
    with patch("vault_compass.app.pull_and_refresh") as pull:
        spawned[0]()
    pull.assert_called_with("h:/b", settings.compass_briefings_dir, settings.compass_db_path)


def test_serve_start_does_not_pull_with_no_remote(vault: Path, tmp_path: Path) -> None:
    spawned: list[Callable[[], None]] = []

    with TestClient(create_app(_settings(vault, tmp_path / "b"), spawn=spawned.append)):
        pass

    assert spawned == []


def test_serve_start_does_not_pull_twice_in_a_day(vault: Path, tmp_path: Path) -> None:
    spawned: list[Callable[[], None]] = []
    settings = _settings(vault, tmp_path / "briefings", remote="h:/b")
    record_run(
        settings.compass_db_path,
        fetched=True,
        report=RefreshReport(0, 0, 0, 0, 0),
        now=datetime.now(UTC) - timedelta(hours=1),
    )

    with TestClient(create_app(settings, spawn=spawned.append)):
        pass

    assert spawned == []


def test_a_failed_background_pull_is_logged_and_does_not_stop_the_app(
    vault: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(vault, tmp_path / "briefings", remote="h:/b")

    with caplog.at_level(logging.WARNING, logger="vault_compass.app"):
        with patch("vault_compass.app.pull_and_refresh", side_effect=BriefingsError("no route")):
            with TestClient(create_app(settings, spawn=lambda work: work())) as api:
                assert api.get("/status").json() == {"running": True}

    assert [
        (r.levelno, r.getMessage()) for r in caplog.records if r.name == "vault_compass.app"
    ] == [(logging.WARNING, "Briefing pull failed: no route")]


def test_a_crashing_background_pull_is_logged_with_its_traceback(
    vault: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(vault, tmp_path / "briefings", remote="h:/b")

    with caplog.at_level(logging.ERROR, logger="vault_compass.app"):
        with patch("vault_compass.app.pull_and_refresh", side_effect=RuntimeError("boom")):
            with TestClient(create_app(settings, spawn=lambda work: work())):
                pass

    records = [r for r in caplog.records if r.name == "vault_compass.app"]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.ERROR, "Briefing pull crashed")
    ]
    assert records[0].exc_info is not None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

cli_runner = CliRunner()


def _cli_settings(tmp_path: Path, remote: str) -> CompassSettings:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return CompassSettings(
        obsidian_vault_path=vault,
        compass_briefings_remote=remote,
        compass_briefings_dir=tmp_path / "briefings",
    )


def test_pull_with_the_remote_empty_says_the_feature_is_off_and_does_nothing(
    tmp_path: Path,
) -> None:
    settings = _cli_settings(tmp_path, "   ")
    with (
        patch.object(compass_cli, "get_compass_settings", return_value=settings),
        patch.object(compass_cli, "pull_and_refresh") as pull,
    ):
        result = cli_runner.invoke(compass_cli.app, ["briefings", "pull"])

    assert (result.exit_code, result.output) == (0, OFF_MESSAGE + "\n")
    pull.assert_not_called()
    assert not settings.compass_db_path.exists()
    assert not settings.compass_briefings_dir.exists()


def test_pull_prints_the_counts(tmp_path: Path) -> None:
    settings = _cli_settings(tmp_path, "h:/b")
    report = RefreshReport(files=3, links=40, checked=38, unreachable=5, left=7)
    with (
        patch.object(compass_cli, "get_compass_settings", return_value=settings),
        patch.object(compass_cli, "pull_and_refresh", return_value=report) as pull,
    ):
        result = cli_runner.invoke(compass_cli.app, ["briefings", "pull"])

    assert result.exit_code == 0
    assert result.output == (
        "Briefing files: 3\n"
        "Links: 40\n"
        "Checked online: 38 (5 did not open)\n"
        "7 links still to check. Run the command again.\n"
    )
    pull.assert_called_with("h:/b", settings.compass_briefings_dir, settings.compass_db_path)


def test_pull_prints_no_leftover_line_when_all_links_were_checked(tmp_path: Path) -> None:
    settings = _cli_settings(tmp_path, "h:/b")
    with (
        patch.object(compass_cli, "get_compass_settings", return_value=settings),
        patch.object(compass_cli, "pull_and_refresh", return_value=RefreshReport(1, 2, 2, 0, 0)),
    ):
        result = cli_runner.invoke(compass_cli.app, ["briefings", "pull"])

    assert result.output == "Briefing files: 1\nLinks: 2\nChecked online: 2 (0 did not open)\n"


def test_pull_failure_exits_with_one_line(tmp_path: Path) -> None:
    settings = _cli_settings(tmp_path, "h:/b")
    with (
        patch.object(compass_cli, "get_compass_settings", return_value=settings),
        patch.object(compass_cli, "pull_and_refresh", side_effect=BriefingsError("no route")),
    ):
        result = cli_runner.invoke(compass_cli.app, ["briefings", "pull"])

    assert (result.exit_code, result.output) == (1, "❌ no route\n")


def test_parse_reads_the_local_folder_without_ssh(tmp_path: Path) -> None:
    settings = _cli_settings(tmp_path, "h:/b")
    report = RefreshReport(files=1, links=2, checked=2, unreachable=1, left=0)
    with (
        patch.object(compass_cli, "get_compass_settings", return_value=settings),
        patch.object(compass_cli, "refresh_links", return_value=report) as refresh,
        patch.object(compass_cli, "pull_and_refresh") as pull,
    ):
        result = cli_runner.invoke(compass_cli.app, ["briefings", "parse"])

    assert result.output == "Briefing files: 1\nLinks: 2\nChecked online: 2 (1 did not open)\n"
    refresh.assert_called_with(settings.compass_db_path, settings.compass_briefings_dir)
    pull.assert_not_called()
    assert _rows(settings.compass_db_path, "SELECT fetched, files, links FROM briefing_runs") == [
        (False, 1, 2)
    ]


def test_briefings_without_a_subcommand_lists_them() -> None:
    result = cli_runner.invoke(compass_cli.app, ["briefings"])

    assert "pull" in result.output
    assert "parse" in result.output


def test_a_bad_configuration_exits_with_one(tmp_path: Path) -> None:
    from pydantic import ValidationError

    def broken() -> CompassSettings:
        return CompassSettings(obsidian_vault_path=tmp_path / "missing")

    with patch.object(compass_cli, "get_compass_settings", side_effect=broken):
        with pytest.raises(ValidationError):
            broken()
        result = cli_runner.invoke(compass_cli.app, ["briefings", "pull"])

    assert result.exit_code == 1
    assert result.output.startswith("❌ Configuration error:")


def test_settings_default_the_folder_outside_the_vault_and_expand_a_home_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COMPASS_BRIEFINGS_DIR", raising=False)
    monkeypatch.delenv("COMPASS_BRIEFINGS_REMOTE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = tmp_path / "vault"
    vault.mkdir()

    default = CompassSettings(obsidian_vault_path=vault)
    custom = CompassSettings(obsidian_vault_path=vault, compass_briefings_dir=Path("~/b"))

    assert default.compass_briefings_remote == ""
    assert default.compass_briefings_dir == tmp_path / ".compass" / "briefings"
    assert custom.compass_briefings_dir == tmp_path / "b"
