"""Reading links out of briefing files: #197."""

import os
from datetime import date
from pathlib import Path

import pytest

from vault_compass.briefing_parse import (
    MAX_FILE_BYTES,
    MAX_SECTION,
    MAX_TITLE,
    BriefingLink,
    blocked_host,
    briefing_date,
    clean_url,
    parse_briefing,
    parse_folder,
)

D = date(2026, 11, 10)

PROMPT = """# Morning briefing job

Look at https://prompt.example.org/bare and [a prompt link](https://prompt.example.org/md).

"""


def _link(url: str, title: str, section: str | None = None, file: str = "f.md") -> BriefingLink:
    return BriefingLink(url, title, section, D, file)


def _parse(answer: str, prompt: str = PROMPT) -> list[BriefingLink]:
    return parse_briefing(f"{prompt}## Response\n\n{answer}", D, "f.md")


# ---------------------------------------------------------------------------
# Only the answer is read
# ---------------------------------------------------------------------------


def test_links_in_the_prompt_part_are_never_read() -> None:
    links = _parse("## News\n- [Real](https://example.com/real)\n")

    assert links == [_link("https://example.com/real", "Real", "News")]


def test_a_file_without_a_response_heading_has_no_links() -> None:
    text = "# Job\n[Looks real](https://example.com/a)\n"

    assert parse_briefing(text, D, "f.md") == []


def test_the_response_heading_may_be_any_level_and_any_case() -> None:
    text = "[prompt](https://prompt.example.org/x)\n### RESPONSE\n[a](https://example.com/a)\n"

    assert parse_briefing(text, D, "f.md") == [_link("https://example.com/a", "a")]


def test_a_word_response_inside_a_sentence_is_not_the_heading() -> None:
    text = "Write your ## Response below. [p](https://prompt.example.org/x)\n"

    assert parse_briefing(text, D, "f.md") == []


# ---------------------------------------------------------------------------
# Titles and sections
# ---------------------------------------------------------------------------


def test_the_link_text_is_the_title() -> None:
    links = _parse("1. **Bold item** [Link title](https://example.com/a)\n")

    assert [x.title for x in links] == ["Link title"]


@pytest.mark.parametrize("text", ["source", "Read more", "here", "ab", "", "https://example.com/a"])
def test_a_generic_link_text_gives_way_to_the_bold_item_title(text: str) -> None:
    links = _parse(f"1. **Agents ship faster** - [{text}](https://example.com/a)\n")

    assert [x.title for x in links] == ["Agents ship faster"]


def test_a_link_text_that_is_the_host_is_generic() -> None:
    links = _parse("**Bold item** [www.example.com](https://www.example.com/a)\n")

    assert [x.title for x in links] == ["Bold item"]


def test_without_a_bold_title_a_generic_text_is_kept() -> None:
    links = _parse("- [source](https://example.com/a)\n")

    assert [x.title for x in links] == ["source"]


def test_a_bare_url_is_titled_by_the_bold_text_or_else_its_host() -> None:
    links = _parse("**Bold item** https://example.com/a\nplain https://www.other.org/b\n")

    assert [(x.url, x.title) for x in links] == [
        ("https://example.com/a", "Bold item"),
        ("https://www.other.org/b", "other.org"),
    ]


def test_a_markdown_link_is_not_read_again_as_a_bare_url() -> None:
    links = _parse("[Title](https://example.com/a)\n")

    assert links == [_link("https://example.com/a", "Title")]


def test_the_section_is_the_nearest_heading_before_the_link() -> None:
    answer = (
        "[Top](https://example.com/0)\n"
        "## AI\n[One](https://example.com/1)\n"
        "### Models ###\n[Two](https://example.com/2)\n"
    )

    links = _parse(answer)

    assert [(x.title, x.section) for x in links] == [
        ("Top", None),
        ("One", "AI"),
        ("Two", "Models"),
    ]


def test_markdown_and_html_in_a_title_is_kept_as_plain_text() -> None:
    answer = "[**Bold** <img src=x onerror=alert(1)> `code` __u__](https://example.com/a)\n"

    links = _parse(answer)

    assert [x.title for x in links] == ["Bold <img src=x onerror=alert(1)> code u"]


def test_titles_and_sections_are_cut_at_their_limits() -> None:
    answer = f"## {'s' * (MAX_SECTION + 1)}\n[{'t' * (MAX_TITLE + 1)}](https://example.com/a)\n"

    links = _parse(answer)

    assert [(len(x.title), len(x.section or "")) for x in links] == [(MAX_TITLE, MAX_SECTION)]


def test_titles_hold_no_control_characters_and_fold_spaces() -> None:
    links = _parse("[a\x1b\t   b\x7fc](https://example.com/a)\n")

    assert [x.title for x in links] == ["a b c"]


def test_a_balanced_parenthesis_in_the_url_is_kept() -> None:
    links = _parse("[Wiki](https://en.wikipedia.org/wiki/Agent_(AI)) text\n")

    assert [x.url for x in links] == ["https://en.wikipedia.org/wiki/Agent_(AI)"]


def test_an_image_is_not_a_link() -> None:
    assert _parse("![chart](https://example.com/chart.png)\n") == []


# ---------------------------------------------------------------------------
# Dropped links
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://news.google.com/rss/articles/CBMi123?oc=5",
        "https://NEWS.google.com/rss/articles/CBMi123",
        "https://news.google.com/articles/CBMi123",
    ],
)
def test_google_news_redirect_links_are_dropped(url: str) -> None:
    assert _parse(f"[Story]({url})\n") == []


def test_other_google_news_pages_are_kept() -> None:
    links = _parse("[Topic page](https://news.google.com/topics/ABC)\n")

    assert [x.url for x in links] == ["https://news.google.com/topics/ABC"]


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/a",
        "mailto:a@example.com",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "//example.com/a",
        "https://user:pw@example.com/a",
        "https://example.com:notaport/a",
    ],
)
def test_only_plain_http_and_https_links_survive(url: str) -> None:
    assert clean_url(url) is None


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "app.localhost",
        "localhost.",
        "127.0.0.1",
        "127.255.255.254",
        "0.0.0.0",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.0",
        "172.31.255.255",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "[::1]",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "2130706433",
        "127.1",
        "0x7f.0.0.1",
        "nas.local",
        "db.internal",
        "printer.lan",
        "router.home.arpa",
        "intranet",
        "",
    ],
)
def test_loopback_and_private_hosts_are_refused(host: str) -> None:
    assert blocked_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "www.example.com",
        "8.8.8.8",
        "172.15.255.255",
        "172.32.0.0",
        "2606:4700:4700::1111",
        "::ffff:8.8.8.8",
        "xn--bcher-kva.example",
    ],
)
def test_public_hosts_are_allowed(host: str) -> None:
    assert blocked_host(host) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/a",
        "http://127.0.0.1:8100/status",
        "http://[::1]/a",
        "https://10.1.2.3/a",
        "http://169.254.169.254/latest/meta-data",
        "http://2130706433/",
    ],
)
def test_a_link_to_a_refused_host_never_reaches_the_list(url: str) -> None:
    assert clean_url(url) is None
    assert _parse(f"[x]({url})\nbare {url}\n") == []


def test_tracking_parameters_and_the_fragment_are_dropped() -> None:
    url = "https://example.com/a?id=7&utm_source=x&UTM_medium=y&fbclid=1&gclid=2&b=&mc_cid=3#top"

    assert clean_url(url) == "https://example.com/a?id=7&b="


def test_a_url_with_only_tracking_has_no_question_mark_left() -> None:
    assert clean_url("https://example.com/a?utm_source=x") == "https://example.com/a"


def test_trailing_punctuation_is_not_part_of_the_url() -> None:
    assert clean_url("https://example.com/a.") == "https://example.com/a"


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def test_the_briefing_date_comes_from_the_file_name(tmp_path: Path) -> None:
    path = tmp_path / "briefing-2026-11-03-morning.md"
    path.write_text("x", encoding="utf-8")

    assert briefing_date(path) == date(2026, 11, 3)


def test_an_impossible_date_in_the_name_falls_back_to_the_modified_date(tmp_path: Path) -> None:
    path = tmp_path / "briefing-2026-13-45.md"
    path.write_text("x", encoding="utf-8")
    stamp = date(2026, 5, 6)
    modified = (stamp - date(1970, 1, 1)).days * 86400 + 43200
    os.utime(path, (modified, modified))

    assert briefing_date(path) == stamp


def test_parse_folder_reads_regular_markdown_files_only(tmp_path: Path) -> None:
    body = "## Response\n[A](https://example.com/a)\n"
    (tmp_path / "2026-11-01.md").write_text(body, encoding="utf-8")
    (tmp_path / "2026-11-02.txt").write_text(body, encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "2026-11-03.md").write_text(body, encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text(body, encoding="utf-8")
    folder = tmp_path / "briefings"
    folder.mkdir()
    (folder / "2026-11-01.md").write_text(body, encoding="utf-8")
    (folder / "2026-11-02.txt").write_text(body, encoding="utf-8")
    (folder / "sub").mkdir()
    (folder / "sub" / "2026-11-03.md").write_text(body, encoding="utf-8")
    (folder / "2026-11-04.md").symlink_to(outside)
    (folder / "2026-11-05.md").write_text(body, encoding="utf-8")
    (folder / "2026-11-06.md").write_bytes(b"\xff\xfe not utf-8")
    big = folder / "2026-11-07.md"
    big.write_text(body + "x" * MAX_FILE_BYTES, encoding="utf-8")

    links = parse_folder(folder)

    assert [(x.file, x.briefing_date) for x in links] == [
        ("2026-11-01.md", date(2026, 11, 1)),
        ("2026-11-05.md", date(2026, 11, 5)),
    ]


def test_a_file_exactly_at_the_size_limit_is_read(tmp_path: Path) -> None:
    body = "## Response\n[A](https://example.com/a)\n"
    path = tmp_path / "2026-11-01.md"
    path.write_text(body + "x" * (MAX_FILE_BYTES - len(body)), encoding="utf-8")

    assert path.stat().st_size == MAX_FILE_BYTES
    assert [x.file for x in parse_folder(tmp_path)] == ["2026-11-01.md"]


def test_a_missing_folder_has_no_links(tmp_path: Path) -> None:
    assert parse_folder(tmp_path / "nope") == []
