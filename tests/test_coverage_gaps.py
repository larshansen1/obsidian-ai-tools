"""Coverage gaps and source candidates (C7, Q8, Q9): issue #136."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from vault_compass.coverage_gaps import (
    MAX_CANDIDATES,
    VERIFY_TIMEOUT_S,
    Gap,
    extract_citations,
    pick_candidates,
    summarize_gaps,
    url_exists,
)
from vault_compass.notes import refresh_notes
from vault_compass.source_types import classify
from vault_compass.topics import load_topics
from vault_compass.vault_tools import AiVault, run_tool

TOPICS_YAML = (
    "topics:\n"
    "  big:\n    name: Big Topic\n    tags: [x]\n"
    "  other:\n    name: Other\n    tags: [y]\n"
    "min_notes: 1\ntrend_start: 2026-04-01\n"
    "ai_exclude_folders: [notes/reflections]\n"
)
METR = "https://metr.org/blog/study"
ARXIV = "https://arxiv.org/abs/2401.1"
BLOG = "https://example.com/post"
INGESTED = "https://example.com/ingested"
SECRET_URL = "https://secret.example.org/private"
UA = {"User-Agent": "Mozilla/5.0 (compatible; VaultCompass/1.0)"}


def _write(vault: Path, rel: str, body: str, tags: str = "[x]", source_url: str = "") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = f"source_url: {source_url}\n" if source_url else ""
    path.write_text(
        f"---\ntitle: {path.stem}\ncreated: 2026-09-01\ntags: {tags}\n{extra}---\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def ai_vault(tmp_path: Path) -> AiVault:
    root = tmp_path / "vault"
    _write(
        root,
        "notes/agents.md",
        f"See the [METR study]({METR}).\nAlso {ARXIV}. And HTTPS://EXAMPLE.COM/ingested/ again.",
    )
    _write(root, "notes/later.md", f"Again [another title]({METR}) and {BLOG}")
    _write(root, "notes/ingested.md", "Body.", tags="[y]", source_url=INGESTED)
    _write(root, "notes/reflections/diary.md", f"Private link {SECRET_URL}")
    (root / ".kai").mkdir()
    topics_path = root / ".kai" / "topics.yaml"
    topics_path.write_text(TOPICS_YAML, encoding="utf-8")
    db_path = root / ".kai" / "compass.duckdb"
    refresh_notes(root, db_path, load_topics(topics_path))
    return AiVault(root, db_path, load_topics(topics_path))


def _response(status: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    return response


def _request_call(method: str, url: str) -> object:
    return call(
        method, url, timeout=VERIFY_TIMEOUT_S, allow_redirects=True, stream=True, headers=UA
    )


# ---------------------------------------------------------------------------
# Citations and source types
# ---------------------------------------------------------------------------


def test_extract_citations_titles_links_by_text_and_bare_urls_by_host() -> None:
    body = f"Read [The Study]({METR}), then {ARXIV}. Done; see {BLOG}!"

    assert extract_citations(body) == {
        METR: "The Study",
        ARXIV: "arxiv.org",
        BLOG: "example.com",
    }


def test_extract_citations_of_text_without_links_is_empty() -> None:
    assert extract_citations("No links, only htp://broken and www.example.com") == {}


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://arxiv.org/abs/1", "study"),
        ("https://www.youtube.com/watch?v=1", "talk"),
        ("https://someone.substack.com/p/x", "essay"),
        ("https://www.ft.com/content/1", "report"),
        ("https://microsoft.com/blog", "unknown"),
        ("https://notarxiv.org/abs/1", "unknown"),
        ("not a url", "unknown"),
    ],
)
def test_classify_matches_host_or_parent_domain_only(url: str, kind: str) -> None:
    assert classify(url) == kind


# ---------------------------------------------------------------------------
# Coverage gaps (tool)
# ---------------------------------------------------------------------------


def test_coverage_gaps_counts_cited_sources_not_in_the_vault(ai_vault: AiVault) -> None:
    assert ai_vault.coverage_gaps("big") == {
        "topic": "big",
        "count": 3,
        "notes": 2,
        "by_type": [
            {"type": "report", "count": 1},
            {"type": "study", "count": 1},
            {"type": "unknown", "count": 1},
        ],
    }


def test_links_under_a_notes_own_source_are_not_gaps(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    repo = "https://github.com/owner/repo"
    _write(
        root,
        "notes/repo.md",
        f"[README]({repo}/blob/main/README.md) and [other]({repo}-two/README.md)",
        source_url=repo,
    )
    (root / ".kai").mkdir()
    topics_path = root / ".kai" / "topics.yaml"
    topics_path.write_text(TOPICS_YAML, encoding="utf-8")
    db_path = root / ".kai" / "compass.duckdb"
    refresh_notes(root, db_path, load_topics(topics_path))
    vault = AiVault(root, db_path, load_topics(topics_path))

    with patch("vault_compass.coverage_gaps.requests.request", return_value=_response(200)):
        result = vault.source_candidates("big")

    assert [c["url"] for c in result["candidates"]] == [f"{repo}-two/README.md"]


def test_coverage_gaps_of_topic_with_no_citations_is_empty(ai_vault: AiVault) -> None:
    assert ai_vault.coverage_gaps("other") == {
        "topic": "other",
        "count": 0,
        "notes": 0,
        "by_type": [],
    }


def test_summarize_gaps_orders_bars_by_count_then_type() -> None:
    gaps = [
        Gap("https://youtube.com/a", "a", "n1.md"),
        Gap("https://arxiv.org/b", "b", "n1.md"),
        Gap("https://youtu.be/c", "c", "n2.md"),
    ]

    assert summarize_gaps("t", gaps)["by_type"] == [
        {"type": "talk", "count": 2},
        {"type": "study", "count": 1},
    ]


def test_unknown_topic_is_a_tool_error(ai_vault: AiVault) -> None:
    assert run_tool(ai_vault, "coverage_gaps", {"topic": "nope"}) == {
        "error": "Unknown topic: nope"
    }
    assert run_tool(ai_vault, "source_candidates", {"topic": "nope"}) == {
        "error": "Unknown topic: nope"
    }


def test_missing_database_is_a_tool_error(tmp_path: Path) -> None:
    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text(TOPICS_YAML, encoding="utf-8")
    vault = AiVault(tmp_path, tmp_path / "missing.duckdb", load_topics(topics_path))

    assert run_tool(vault, "coverage_gaps", {"topic": "big"}) == {
        "error": "No topic data yet. Run `compass scan` first."
    }


# ---------------------------------------------------------------------------
# Source candidates (tool): only sources confirmed online are shown (C7)
# ---------------------------------------------------------------------------


def test_source_candidates_drop_a_source_that_fails_the_online_check(
    ai_vault: AiVault, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_request(method: str, url: str, **_: object) -> MagicMock:
        return _response(404 if url == ARXIV else 200)

    with (
        patch("vault_compass.coverage_gaps.requests.request", side_effect=fake_request) as req,
        caplog.at_level(logging.INFO, logger="vault_compass.coverage_gaps"),
    ):
        result = run_tool(ai_vault, "source_candidates", {"topic": "big"})

    assert result == {
        "candidates": [
            {
                "url": METR,
                "title": "METR study",
                "type": "report",
                "origin": "citation",
                "cited_in": "notes/agents.md",
                "why": "Cited in [[notes/agents.md]] but not in the vault yet.",
            },
            {
                "url": BLOG,
                "title": "example.com",
                "type": "unknown",
                "origin": "citation",
                "cited_in": "notes/later.md",
                "why": "Cited in [[notes/later.md]] but not in the vault yet.",
            },
        ],
        "not_found": 1,
    }
    assert req.call_args_list == [
        _request_call("HEAD", METR),
        _request_call("HEAD", ARXIV),
        _request_call("GET", ARXIV),
        _request_call("HEAD", BLOG),
    ]
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.INFO, f"Dropped source candidate {ARXIV}: not found online")
    ]


def test_excluded_notes_never_contribute_sources(ai_vault: AiVault) -> None:
    with patch("vault_compass.coverage_gaps.requests.request", return_value=_response(200)) as req:
        result = ai_vault.source_candidates("big")

    assert [c["url"] for c in result["candidates"]] == [METR, ARXIV, BLOG]
    assert _request_call("HEAD", SECRET_URL) not in (req.call_args_list)


def test_pick_candidates_stops_at_the_limit_without_checking_more() -> None:
    gaps = [Gap(f"https://arxiv.org/{i}", str(i), "n.md") for i in range(3)]
    exists = MagicMock(return_value=True)

    result = pick_candidates(gaps, exists=exists, limit=2)

    assert [c["url"] for c in result["candidates"]] == [
        "https://arxiv.org/0",
        "https://arxiv.org/1",
    ]
    assert result["not_found"] == 0
    assert exists.call_args_list == [call("https://arxiv.org/0"), call("https://arxiv.org/1")]


def test_pick_candidates_default_limit_is_ten() -> None:
    gaps = [Gap(f"https://arxiv.org/{i}", str(i), "n.md") for i in range(MAX_CANDIDATES + 1)]

    result = pick_candidates(gaps, exists=lambda _url: True)

    assert MAX_CANDIDATES == 10
    assert len(result["candidates"]) == 10


# ---------------------------------------------------------------------------
# url_exists
# ---------------------------------------------------------------------------


def test_url_exists_accepts_status_399_from_head() -> None:
    with patch("vault_compass.coverage_gaps.requests.request", return_value=_response(399)) as req:
        assert url_exists(METR) is True

    req.assert_called_once_with(
        "HEAD", METR, timeout=10, allow_redirects=True, stream=True, headers=UA
    )


def test_url_exists_retries_with_get_when_head_returns_400() -> None:
    responses = [_response(400), _response(200)]
    with patch("vault_compass.coverage_gaps.requests.request", side_effect=responses) as req:
        assert url_exists(METR) is True

    assert req.call_args_list == [_request_call("HEAD", METR), _request_call("GET", METR)]
    responses[0].close.assert_called_once_with()
    responses[1].close.assert_called_once_with()


def test_url_exists_is_false_when_both_methods_fail() -> None:
    effects = [requests.ConnectionError("down"), _response(404)]
    with patch("vault_compass.coverage_gaps.requests.request", side_effect=effects) as req:
        assert url_exists(METR, timeout=2.5) is False

    assert req.call_args_list == [
        call("HEAD", METR, timeout=2.5, allow_redirects=True, stream=True, headers=UA),
        call("GET", METR, timeout=2.5, allow_redirects=True, stream=True, headers=UA),
    ]


def test_url_exists_is_false_when_the_network_is_down() -> None:
    with patch(
        "vault_compass.coverage_gaps.requests.request",
        side_effect=requests.Timeout("slow"),
    ) as req:
        assert url_exists(METR) is False

    assert req.call_count == 2
