"""OpenRouter chat client with streaming and tool calls (N2).

The model comes from settings (`llm_model`), so it can change without code
changes. Tests and other callers use anything that satisfies `ChatModel`.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

from .config import CompassSettings


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    # USD as reported by OpenRouter; None when the provider did not say.
    cost_usd: float | None


ModelEvent = TextDelta | ToolCall | Usage


class ChatModel(Protocol):
    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ModelEvent]: ...


class AiNotConfiguredError(Exception):
    """No OpenRouter key is set. The message is user-facing."""


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class OpenRouterModel:
    def __init__(self, settings: CompassSettings) -> None:
        if not settings.openrouter_api_key:
            raise AiNotConfiguredError("Set OPENROUTER_API_KEY in .env to use the chat.")
        self._model = settings.llm_model
        self._max_tokens = settings.compass_ai_max_output_tokens
        self._client = AsyncOpenAI(
            api_key=settings.openrouter_api_key, base_url=settings.llm_base_url
        )

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ModelEvent]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            # OpenRouter adds the dollar cost to the usage block when asked.
            "extra_body": {"usage": {"include": True}},
        }
        if tools:
            kwargs["tools"] = tools
        response = await self._client.chat.completions.create(**kwargs)
        pending: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        async for chunk in response:
            if chunk.usage is not None:
                extra = chunk.usage.model_extra or {}
                cost = extra.get("cost")
                usage = Usage(
                    model=self._model,
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                    cost_usd=float(cost) if cost is not None else None,
                )
            for choice in chunk.choices:
                delta = choice.delta
                if delta.content:
                    yield TextDelta(delta.content)
                for call in delta.tool_calls or []:
                    slot = pending.setdefault(call.index, {"id": "", "name": "", "args": ""})
                    slot["id"] = call.id or slot["id"]
                    if call.function:
                        slot["name"] = call.function.name or slot["name"]
                        slot["args"] += call.function.arguments or ""
        for index in sorted(pending):
            slot = pending[index]
            yield ToolCall(slot["id"], slot["name"], _parse_arguments(slot["args"]))
        if usage is not None:
            yield usage
