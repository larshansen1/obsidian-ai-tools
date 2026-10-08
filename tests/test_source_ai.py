"""Paid calls behind the source tools: budget, web search and source types (#143)."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from vault_compass.ai_client import AiNotConfiguredError
from vault_compass.ai_cost import month_spend
from vault_compass.config import CompassSettings
from vault_compass.source_ai import (
    SEARCH_PROMPT,
    TYPES_PROMPT,
    ActionBudget,
    OpenRouterSourceAi,
    SourceAiError,
    WebHit,
    parse_types,
    web_hits,
)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CompassSettings:
    for key in (
        "COMPASS_DB_PATH",
        "LLM_MODEL",
        "COMPASS_AI_ACTION_LIMIT_USD",
        "COMPASS_AI_MONTHLY_LIMIT_USD",
        "COMPASS_AI_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(key, raising=False)
    return CompassSettings(obsidian_vault_path=tmp_path, openrouter_api_key="k", llm_model="m/x")


def _client(message: dict[str, Any], cost: float | None = 0.03) -> MagicMock:
    response = MagicMock()
    response.usage.prompt_tokens = 120
    response.usage.completion_tokens = 40
    response.usage.model_extra = {} if cost is None else {"cost": cost}
    response.choices[0].message.model_dump.return_value = message
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def _citation(url: str, title: str | None = "T") -> dict[str, Any]:
    return {"type": "url_citation", "url_citation": {"url": url, "title": title}}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_web_hits_keep_url_citations_once_each() -> None:
    message = {
        "content": "Read https://memory.example/made-up",
        "annotations": [
            _citation("https://a.example/1", "First"),
            {"type": "file", "file": {"name": "x"}},
            _citation("https://a.example/1", "Duplicate"),
            _citation("https://b.example/2", None),
        ],
    }

    assert web_hits(message) == [
        WebHit("https://a.example/1", "First"),
        WebHit("https://b.example/2", "https://b.example/2"),
    ]


@pytest.mark.parametrize("annotations", [None, []])
def test_web_hits_without_citations_is_empty(annotations: object) -> None:
    assert web_hits({"content": "https://memory.example/x", "annotations": annotations}) == []


def test_parse_types_maps_numbers_to_urls() -> None:
    text = '```json\n{"types": {"1": "study", "2": "talk"}}\n```'

    assert parse_types(text, ["u1", "u2"]) == {"u1": "study", "u2": "talk"}


@pytest.mark.parametrize(
    "text",
    [
        '{"types": {"0": "study"}}',
        '{"types": {"3": "study"}}',
        '{"types": {"one": "study"}}',
        '{"types": {"1": "podcast"}}',
        '{"types": ["study"]}',
        '{"other": 1}',
        "no json here",
        "{not json}",
        "[1, 2]",
    ],
)
def test_parse_types_drops_anything_it_cannot_trust(text: str) -> None:
    assert parse_types(text, ["u1", "u2"]) == {}


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_allows_a_call_exactly_at_the_action_limit(settings: CompassSettings) -> None:
    assert ActionBudget(settings, month_cost=0.0).refuse(0.25) is None


def test_budget_refuses_a_call_over_the_action_limit(settings: CompassSettings) -> None:
    budget = ActionBudget(settings, month_cost=0.0, spent=0.2)

    assert (
        budget.refuse(0.06) == "This action may cost up to $0.26, over the $0.25 per-action limit."
    )


def test_budget_refuses_a_call_over_the_monthly_limit(settings: CompassSettings) -> None:
    budget = ActionBudget(settings, month_cost=9.99)

    assert budget.refuse(0.02) == (
        "This would bring this month's AI spend to $10.01, over the $10.00 monthly limit."
    )


def test_approved_budget_never_refuses(settings: CompassSettings) -> None:
    assert ActionBudget(settings, month_cost=50.0, approved=True).refuse(5.0) is None


# ---------------------------------------------------------------------------
# OpenRouterSourceAi
# ---------------------------------------------------------------------------


def test_source_ai_needs_an_api_key(tmp_path: Path) -> None:
    settings = CompassSettings(obsidian_vault_path=tmp_path, openrouter_api_key=None)

    with pytest.raises(AiNotConfiguredError) as exc_info:
        OpenRouterSourceAi(settings, ActionBudget(settings, 0.0), client=MagicMock())

    assert str(exc_info.value) == "Set OPENROUTER_API_KEY in .env to use the chat."


def test_search_uses_the_web_plugin_and_logs_its_cost(settings: CompassSettings) -> None:
    client = _client({"content": "ok", "annotations": [_citation("https://a.example/1", "A")]})
    budget = ActionBudget(settings, month_cost=0.0, spent=0.01)

    hits = OpenRouterSourceAi(settings, budget, client=client).search("Sleep", ["rest", "adhd"])

    assert hits == [WebHit("https://a.example/1", "A")]
    client.chat.completions.create.assert_called_once_with(
        model="m/x",
        messages=[
            {"role": "system", "content": SEARCH_PROMPT},
            {"role": "user", "content": "Topic: Sleep\nThemes: rest, adhd"},
        ],
        max_tokens=1500,
        extra_body={"usage": {"include": True}, "plugins": [{"id": "web", "max_results": 5}]},
    )
    assert budget.spent == pytest.approx(0.04)
    assert month_spend(settings.compass_db_path) == pytest.approx(0.03)


def test_search_without_themes_says_none(settings: CompassSettings) -> None:
    client = _client({"content": "", "annotations": []})

    OpenRouterSourceAi(settings, ActionBudget(settings, 0.0), client=client).search("Sleep", [])

    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[1] == {"role": "user", "content": "Topic: Sleep\nThemes: none"}


def test_search_over_budget_never_calls_the_model(settings: CompassSettings) -> None:
    client = _client({})
    budget = ActionBudget(settings, month_cost=10.0)

    with pytest.raises(SourceAiError) as exc_info:
        OpenRouterSourceAi(settings, budget, client=client).search("Sleep", [])

    assert str(exc_info.value).endswith("over the $10.00 monthly limit.")
    client.chat.completions.create.assert_not_called()
    assert budget.spent == 0.0


def test_failed_call_is_a_source_ai_error(settings: CompassSettings) -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")

    with pytest.raises(SourceAiError) as exc_info:
        OpenRouterSourceAi(settings, ActionBudget(settings, 0.0), client=client).search("S", [])

    assert str(exc_info.value) == "The web_search call failed: boom"


def test_classify_numbers_the_sources_and_reads_the_types(settings: CompassSettings) -> None:
    client = _client({"content": '{"types": {"2": "essay"}}'}, cost=None)
    budget = ActionBudget(settings, month_cost=0.0)
    sources = [("https://a.example/1", "A"), ("https://b.example/2", "B")]

    types = OpenRouterSourceAi(settings, budget, client=client).classify(sources)

    assert types == {"https://b.example/2": "essay"}
    numbered = {
        "1": {"url": "https://a.example/1", "title": "A"},
        "2": {"url": "https://b.example/2", "title": "B"},
    }
    client.chat.completions.create.assert_called_once_with(
        model="m/x",
        messages=[
            {"role": "system", "content": TYPES_PROMPT},
            {"role": "user", "content": json.dumps(numbered)},
        ],
        max_tokens=1500,
        extra_body={"usage": {"include": True}},
    )
    # No reported cost: priced from tokens at $3 / $15 per million.
    assert budget.spent == pytest.approx((120 * 3 + 40 * 15) / 1_000_000)


def test_classify_with_empty_answer_is_empty(settings: CompassSettings) -> None:
    client = _client({"content": None})

    types = OpenRouterSourceAi(settings, ActionBudget(settings, 0.0), client=client).classify(
        [("https://a.example/1", "A")]
    )

    assert types == {}
