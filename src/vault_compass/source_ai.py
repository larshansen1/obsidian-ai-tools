"""Paid model calls behind the source tools: web search (Q9) and source types (Q8).

Every call checks the per-action and monthly limits first (N3) through an
`ActionBudget` shared with the chat, and is logged with its cost. Web sources
come only from the search's URL citations, never from the model's own text.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI

from .ai_client import AiNotConfiguredError, Usage
from .ai_cost import check_limits, estimate_cost, record_call
from .config import CompassSettings

SOURCE_TYPES = ("study", "report", "essay", "talk")
WEB_RESULTS = 5
# OpenRouter's web plugin price per result; only used to estimate a call before it runs.
WEB_USD_PER_RESULT = 0.004

SEARCH_PROMPT = """\
Use web search to find studies, reports, essays or talks about the topic below that a \
careful reader should know. Prefer primary sources. Reply with one short line per source."""

TYPES_PROMPT = """\
Give the type of each numbered source: study, report, essay or talk. Answer only with JSON \
like {"types": {"1": "study", "2": "talk"}}. Leave out a source you cannot tell."""


class SourceAiError(Exception):
    """A paid call did not run or failed. The message is shown to the model."""


@dataclass(frozen=True)
class WebHit:
    url: str
    title: str


class SourceAi(Protocol):
    def search(self, topic_name: str, themes: list[str]) -> list[WebHit]: ...

    def classify(self, sources: list[tuple[str, str]]) -> dict[str, str]: ...


@dataclass
class ActionBudget:
    """Spend for one user action, shared by the chat loop and the tools it runs."""

    settings: CompassSettings
    month_cost: float
    approved: bool = False
    spent: float = field(default=0.0)

    def refuse(self, estimate: float) -> str | None:
        """Why the next call may not run, or None when it may."""
        if self.approved:
            return None
        limit = check_limits(
            self.settings, action_cost=self.spent, month_cost=self.month_cost, next_call=estimate
        )
        return limit.reason


def web_hits(message: dict[str, Any]) -> list[WebHit]:
    """The URL citations in a model answer, first one per URL."""
    hits: dict[str, WebHit] = {}
    for note in message.get("annotations") or []:
        cite = note.get("url_citation") if note.get("type") == "url_citation" else None
        url = (cite or {}).get("url")
        if url and url not in hits:
            hits[url] = WebHit(url, (cite or {}).get("title") or url)
    return list(hits.values())


def parse_types(text: str, urls: list[str]) -> dict[str, str]:
    """URL -> type from a numbered JSON answer; unknown numbers and types are dropped."""
    start, end = text.find("{"), text.rfind("}")
    try:
        answer = json.loads(text[start : end + 1]).get("types") if start != -1 else None
    except (ValueError, AttributeError):
        return {}
    if not isinstance(answer, dict):
        return {}
    out: dict[str, str] = {}
    for key, kind in answer.items():
        index = int(key) - 1 if str(key).isdigit() else -1
        if 0 <= index < len(urls) and kind in SOURCE_TYPES:
            out[urls[index]] = kind
    return out


def _usage(response: Any, model: str) -> Usage:
    usage = response.usage
    cost = (usage.model_extra or {}).get("cost")
    return Usage(
        model=model,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cost_usd=float(cost) if cost is not None else None,
    )


class OpenRouterSourceAi:
    """Synchronous: the tools run in a worker thread."""

    def __init__(
        self, settings: CompassSettings, budget: ActionBudget, client: Any | None = None
    ) -> None:
        if not settings.openrouter_api_key:
            raise AiNotConfiguredError("Set OPENROUTER_API_KEY in .env to use the chat.")
        self.settings = settings
        self.budget = budget
        self._client = client or OpenAI(
            api_key=settings.openrouter_api_key, base_url=settings.llm_base_url
        )

    def _ask(self, name: str, system: str, user: str, extra: dict[str, Any]) -> dict[str, Any]:
        fee = WEB_RESULTS * WEB_USD_PER_RESULT if "plugins" in extra else 0.0
        reason = self.budget.refuse(estimate_cost(self.settings, len(system) + len(user)) + fee)
        if reason:
            raise SourceAiError(reason)
        try:
            response = self._client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=self.settings.compass_ai_max_output_tokens,
                extra_body={"usage": {"include": True}, **extra},
            )
        except Exception as e:
            raise SourceAiError(f"The {name} call failed: {e}") from e
        usage = _usage(response, self.settings.llm_model)
        self.budget.spent += record_call(self.settings, usage, name, user[:200])
        message: dict[str, Any] = response.choices[0].message.model_dump()
        return message

    def search(self, topic_name: str, themes: list[str]) -> list[WebHit]:
        user = f"Topic: {topic_name}\nThemes: {', '.join(themes) or 'none'}"
        plugins = [{"id": "web", "max_results": WEB_RESULTS}]
        return web_hits(self._ask("web_search", SEARCH_PROMPT, user, {"plugins": plugins}))

    def classify(self, sources: list[tuple[str, str]]) -> dict[str, str]:
        urls = [url for url, _ in sources]
        numbered = {str(i + 1): {"url": u, "title": t} for i, (u, t) in enumerate(sources)}
        message = self._ask("source_types", TYPES_PROMPT, json.dumps(numbered), {})
        return parse_types(message.get("content") or "", urls)
