"""Coverage gaps and source candidates (C7, Q8, Q9): issue #136."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests
from fastapi.testclient import TestClient

from vault_compass.app import create_app
from vault_compass.config import CompassSettings
from vault_compass.coverage_gaps import (
    ESUMMARY_URL,
    MAX_CANDIDATES,
    OEMBED_URL,
    VERIFY_TIMEOUT_S,
    Gap,
    card,
    extract_citations,
    find_candidates,
    pmc_exists,
    summarize_gaps,
    url_exists,
    verify,
    youtube_exists,
)
from vault_compass.notes import refresh_notes
from vault_compass.source_ai import SourceAiError, WebHit
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
WEB1 = "https://web.example.net/one"
WEB2 = "https://web.example.net/two"
PMC_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC7247165/"
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


class FakeSourceAi:
    """Records every paid call; `fail_search` / `fail_types` raise like a refused budget."""

    def __init__(
        self,
        hits: list[WebHit] | None = None,
        types: dict[str, str] | None = None,
        fail_search: str | None = None,
        fail_types: str | None = None,
    ) -> None:
        self.hits = hits or []
        self.types = types or {}
        self.fail_search = fail_search
        self.fail_types = fail_types
        self.searches: list[tuple[str, list[str]]] = []
        self.classified: list[list[tuple[str, str]]] = []

    def search(self, topic_name: str, themes: list[str]) -> list[WebHit]:
        self.searches.append((topic_name, themes))
        if self.fail_search:
            raise SourceAiError(self.fail_search)
        return self.hits

    def classify(self, sources: list[tuple[str, str]]) -> dict[str, str]:
        self.classified.append(sources)
        if self.fail_types:
            raise SourceAiError(self.fail_types)
        return {url: self.types[url] for url, _ in sources if url in self.types}


def _with_ai(vault: AiVault, ai: FakeSourceAi) -> AiVault:
    return AiVault(vault.vault_path, vault.db_path, vault.definitions, source_ai=ai)


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
        "notes_for_model": [],
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
        "notes_for_model": [],
    }


def test_summarize_gaps_orders_bars_by_count_then_type() -> None:
    gaps = [
        Gap("https://youtube.com/a", "a", "n1.md"),
        Gap("https://arxiv.org/b", "b", "n1.md"),
        Gap("https://youtu.be/c", "c", "n2.md"),
    ]

    types = {g.url: classify(g.url) for g in gaps}

    assert summarize_gaps("t", gaps, types)["by_type"] == [
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
        "notes_for_model": ["Web search is not available here (no OpenRouter key)."],
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


def test_verify_stops_at_the_limit_without_checking_more() -> None:
    gaps = [Gap(f"https://arxiv.org/{i}", str(i), "n.md") for i in range(3)]
    exists = MagicMock(return_value=True)

    kept, dropped = verify(gaps, exists, 2)

    assert [g.url for g in kept] == ["https://arxiv.org/0", "https://arxiv.org/1"]
    assert dropped == 0
    assert exists.call_args_list == [call("https://arxiv.org/0"), call("https://arxiv.org/1")]


def test_find_candidates_default_limit_is_ten() -> None:
    gaps = [Gap(f"https://arxiv.org/{i}", str(i), "n.md") for i in range(MAX_CANDIDATES + 1)]
    search = MagicMock()

    shown, not_found, notes = find_candidates(gaps, set(), search, exists=lambda _url: True)

    assert MAX_CANDIDATES == 10
    assert len(shown) == 10
    assert (not_found, notes) == (0, [])
    search.assert_not_called()


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


# ---------------------------------------------------------------------------
# PubMed Central lookup
# ---------------------------------------------------------------------------


def _esummary(status: int, payload: object) -> MagicMock:
    response = _response(status)
    response.json.return_value = payload
    return response


def _esummary_call(pmcid: str) -> object:
    return call(
        ESUMMARY_URL,
        params={"db": "pmc", "id": pmcid, "retmode": "json"},
        timeout=VERIFY_TIMEOUT_S,
        headers=UA,
    )


def test_pmc_exists_for_a_real_article() -> None:
    found = _esummary(200, {"result": {"uids": ["7247165"], "7247165": {"uid": "7247165"}}})
    with patch("vault_compass.coverage_gaps.requests.get", return_value=found) as get:
        assert pmc_exists("7247165") is True

    assert get.call_args_list == [_esummary_call("7247165")]


@pytest.mark.parametrize(
    "response",
    [
        _esummary(200, {"result": {"9": {"uid": "9", "error": "cannot get document summary"}}}),
        _esummary(500, {"result": {"9": {"uid": "9"}}}),
        _esummary(200, {"result": {}}),
        _esummary(200, {"result": {"9": "not a record"}}),
    ],
)
def test_pmc_exists_is_false_for_a_fake_or_failed_lookup(response: MagicMock) -> None:
    with patch("vault_compass.coverage_gaps.requests.get", return_value=response) as get:
        assert pmc_exists("9") is False

    assert get.call_args_list == [_esummary_call("9")]


def test_pmc_exists_is_false_when_the_answer_is_not_json() -> None:
    response = _response(200)
    response.json.side_effect = ValueError("not json")
    with patch("vault_compass.coverage_gaps.requests.get", return_value=response):
        assert pmc_exists("9") is False


def test_pmc_exists_is_false_when_the_network_is_down() -> None:
    with patch(
        "vault_compass.coverage_gaps.requests.get", side_effect=requests.ConnectionError("down")
    ):
        assert pmc_exists("9", timeout=3) is False


@pytest.mark.parametrize(
    ("url", "pmcid"),
    [
        (PMC_URL, "7247165"),
        ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5509825/", "5509825"),
    ],
)
def test_url_exists_looks_up_pmc_articles_instead_of_fetching_them(url: str, pmcid: str) -> None:
    found = _esummary(200, {"result": {pmcid: {"uid": pmcid}}})
    with (
        patch("vault_compass.coverage_gaps.requests.get", return_value=found) as get,
        patch("vault_compass.coverage_gaps.requests.request") as req,
    ):
        assert url_exists(url) is True

    assert get.call_args_list == [_esummary_call(pmcid)]
    req.assert_not_called()


# ---------------------------------------------------------------------------
# YouTube lookup
# ---------------------------------------------------------------------------


def _oembed_call(url: str, timeout: float = VERIFY_TIMEOUT_S) -> object:
    return call(OEMBED_URL, params={"url": url, "format": "json"}, timeout=timeout, headers=UA)


@pytest.mark.parametrize(("status", "found"), [(200, True), (400, False), (404, False)])
def test_youtube_exists_only_for_a_known_video(status: int, found: bool) -> None:
    video = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with patch("vault_compass.coverage_gaps.requests.get", return_value=_response(status)) as get:
        assert youtube_exists(video) is found

    assert get.call_args_list == [_oembed_call(video)]


def test_youtube_exists_is_false_when_the_network_is_down() -> None:
    with patch(
        "vault_compass.coverage_gaps.requests.get", side_effect=requests.Timeout("slow")
    ) as get:
        assert youtube_exists("https://youtu.be/x", timeout=4) is False

    assert get.call_args_list == [_oembed_call("https://youtu.be/x", timeout=4)]


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/watch?v=xxx",
        "https://m.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://www.youtube.com/shorts/abc",
    ],
)
def test_url_exists_looks_up_youtube_videos_instead_of_fetching_them(url: str) -> None:
    with (
        patch("vault_compass.coverage_gaps.requests.get", return_value=_response(400)) as get,
        patch("vault_compass.coverage_gaps.requests.request") as req,
    ):
        assert url_exists(url) is False

    assert get.call_args_list == [_oembed_call(url)]
    req.assert_not_called()


def test_url_exists_fetches_a_youtube_channel_page_normally() -> None:
    channel = "https://www.youtube.com/@somechannel"
    with (
        patch("vault_compass.coverage_gaps.requests.get") as get,
        patch("vault_compass.coverage_gaps.requests.request", return_value=_response(200)) as req,
    ):
        assert url_exists(channel) is True

    get.assert_not_called()
    assert req.call_args_list == [_request_call("HEAD", channel)]


# ---------------------------------------------------------------------------
# Web search after citations (Q9) and source types from the model (Q8)
# ---------------------------------------------------------------------------


def test_topic_with_no_citations_gets_web_candidates_confirmed_online(ai_vault: AiVault) -> None:
    ai = FakeSourceAi(
        hits=[
            WebHit(WEB1, "Web One"),
            WebHit(INGESTED + "/", "Already in the vault"),
            WebHit(WEB1, "Same source again"),
            WebHit(WEB2, "Gone"),
        ],
        types={WEB1: "essay"},
    )

    def fake_request(method: str, url: str, **_: object) -> MagicMock:
        return _response(404 if url == WEB2 else 200)

    with patch("vault_compass.coverage_gaps.requests.request", side_effect=fake_request):
        result = _with_ai(ai_vault, ai).source_candidates("other")

    assert result == {
        "candidates": [
            {
                "url": WEB1,
                "title": "Web One",
                "type": "essay",
                "origin": "web",
                "cited_in": None,
                "why": "Found by web search on Other; not in the vault yet.",
            }
        ],
        "not_found": 1,
        "notes_for_model": [],
    }
    assert ai.searches == [("Other", ["y"])]
    assert ai.classified == [[(WEB1, "Web One")]]


def test_citations_come_before_web_results(ai_vault: AiVault) -> None:
    ai = FakeSourceAi(hits=[WebHit(METR, "Cited already"), WebHit(WEB1, "Web One")])

    with patch("vault_compass.coverage_gaps.requests.request", return_value=_response(200)):
        result = _with_ai(ai_vault, ai).source_candidates("big")

    assert [(c["url"], c["origin"]) for c in result["candidates"]] == [
        (METR, "citation"),
        (ARXIV, "citation"),
        (BLOG, "citation"),
        (WEB1, "web"),
    ]
    assert ai.searches == [("Big Topic", ["x"])]


def test_web_search_refused_by_the_budget_is_a_note(ai_vault: AiVault) -> None:
    ai = FakeSourceAi(fail_search="over the $0.25 per-action limit.")

    with patch("vault_compass.coverage_gaps.requests.request", return_value=_response(200)):
        result = _with_ai(ai_vault, ai).source_candidates("other")

    assert result["candidates"] == []
    assert result["notes_for_model"] == ["Web search skipped: over the $0.25 per-action limit."]


def test_unclear_type_is_asked_once_then_read_from_the_cache(ai_vault: AiVault) -> None:
    ai = FakeSourceAi(types={BLOG: "essay"})
    vault = _with_ai(ai_vault, ai)

    first = vault.coverage_gaps("big")
    second = vault.coverage_gaps("big")

    assert first["by_type"] == [
        {"type": "essay", "count": 1},
        {"type": "report", "count": 1},
        {"type": "study", "count": 1},
    ]
    assert second == first
    assert ai.classified == [[(BLOG, "example.com")]]


def test_a_type_the_model_leaves_out_is_cached_as_unknown(ai_vault: AiVault) -> None:
    ai = FakeSourceAi(types={})
    vault = _with_ai(ai_vault, ai)

    vault.coverage_gaps("big")
    result = vault.coverage_gaps("big")

    assert {"type": "unknown", "count": 1} in result["by_type"]
    assert ai.classified == [[(BLOG, "example.com")]]


def test_failed_type_check_is_a_note_and_is_not_cached(ai_vault: AiVault) -> None:
    ai = FakeSourceAi(fail_types="The source_types call failed: boom")
    vault = _with_ai(ai_vault, ai)

    first = vault.coverage_gaps("big")
    vault.coverage_gaps("big")

    assert first["notes_for_model"] == [
        "Source types not checked: The source_types call failed: boom"
    ]
    assert {"type": "unknown", "count": 1} in first["by_type"]
    assert len(ai.classified) == 2


def test_web_card_has_no_citing_note() -> None:
    assert card(Gap(WEB1, "Web One", None), "talk", "Big Topic") == {
        "url": WEB1,
        "title": "Web One",
        "type": "talk",
        "origin": "web",
        "cited_in": None,
        "why": "Found by web search on Big Topic; not in the vault yet.",
    }


# ---------------------------------------------------------------------------
# Topic page endpoint
# ---------------------------------------------------------------------------


def _app_client(vault_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault_root))
    for key in ("COMPASS_DB_PATH", "COMPASS_TOPICS_PATH"):
        monkeypatch.delenv(key, raising=False)
    return TestClient(create_app(CompassSettings()))


def test_topic_coverage_endpoint_returns_the_bars_without_model_calls(
    ai_vault: AiVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _app_client(ai_vault.vault_path, monkeypatch).get("/topics/big/coverage")

    assert response.status_code == 200
    assert response.json() == ai_vault.coverage_gaps("big")


def test_topic_coverage_endpoint_404s_for_an_unknown_topic(
    ai_vault: AiVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _app_client(ai_vault.vault_path, monkeypatch).get("/topics/nope/coverage")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown topic: nope"}


def test_topic_coverage_endpoint_409s_before_the_first_scan(
    ai_vault: AiVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    ai_vault.db_path.unlink()

    response = _app_client(ai_vault.vault_path, monkeypatch).get("/topics/big/coverage")

    assert response.status_code == 409
    assert response.json() == {"detail": "No topic data yet. Run `compass scan` first."}
