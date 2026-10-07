"""The chat endpoint's engine: turns a thread into a UI message stream (C2, C3, C6).

Wire format: SSE lines of AI SDK "UI message stream" chunks, as proven in
docs/vault-compass/spike-notes.md. Do not send `start-step` right after
`start`; only between steps.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .ai_client import AiNotConfiguredError, ChatModel, TextDelta, ToolCall, Usage
from .ai_cost import check_limits, estimate_cost, month_spend, record_call
from .config import CompassSettings
from .vault_tools import AiVault, run_tool, tool_schemas

logger = logging.getLogger(__name__)

CONFIRM_TOOL = "confirm_cost"
STREAM_HEADERS = {"x-vercel-ai-ui-message-stream": "v1", "Cache-Control": "no-cache"}

SYSTEM_PROMPT = """\
You are the assistant inside Vault Compass, an app that shows what an Obsidian vault \
contains. Answer from the vault: use the tools before you make a statement about it. If \
the tools find nothing, say so; do not guess.

Work with as few tool calls as you can. For a question about a topic's themes or what it \
covers, call topic_tags first, then search or read only to check one or two points. Do \
not repeat near-identical searches.

Cite every statement about the vault with the `cite` value a tool gave you, copied \
exactly, for example [[notes/evergreen/example.md]]. Never cite by file name or title \
alone, and cite only notes you actually got from a tool. Keep answers short and plain.

{context}"""


def sse(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk)}\n\n"


def describe_context(screen: str | None, topic: str | None, topic_name: str | None) -> str:
    if screen is None and topic is None:
        return "The user's current screen is unknown."
    parts = [f"The user is on the {screen or 'unknown'} screen."]
    if topic is not None:
        parts.append(
            f'They are looking at the topic "{topic_name or topic}" (topic id: {topic}). '
            "Questions without a topic name are about this topic."
        )
    return " ".join(parts)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if p.get("type") == "text")


def _result_value(output: Any) -> Any:
    # AI SDK v5 shape: {"type": "json", "value": ...}.
    if isinstance(output, dict) and "value" in output:
        return output["value"]
    return output


def cost_approval(messages: list[dict[str, Any]]) -> bool | None:
    """The user's answer to the last cost question: True, False, or None if unanswered."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return None
        content = msg.get("content")
        if msg.get("role") != "tool" or not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "tool-result" and part.get("toolName") == CONFIRM_TOOL:
                value = _result_value(part.get("output"))
                return bool(isinstance(value, dict) and value.get("approved") is True)
    return None


def to_model_messages(messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    """Thread (AI SDK shape) -> OpenAI chat messages. The cost question is dropped."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        role, content = msg.get("role"), msg.get("content")
        if role == "user":
            out.append({"role": "user", "content": _text_of(content)})
        elif role == "assistant":
            parts = content if isinstance(content, list) else []
            calls = [
                {
                    "id": p["toolCallId"],
                    "type": "function",
                    "function": {
                        "name": p["toolName"],
                        "arguments": json.dumps(p.get("input") or {}),
                    },
                }
                for p in parts
                if p.get("type") == "tool-call" and p.get("toolName") != CONFIRM_TOOL
            ]
            text = _text_of(content)
            if text or calls:
                entry: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = calls
                out.append(entry)
        elif role == "tool" and isinstance(content, list):
            for part in content:
                if part.get("type") == "tool-result" and part.get("toolName") != CONFIRM_TOOL:
                    value = _result_value(part.get("output"))
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": part["toolCallId"],
                            "content": json.dumps(value),
                        }
                    )
    return out


def _chars(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(messages))


async def _text(text: str) -> AsyncIterator[str]:
    part_id = uuid.uuid4().hex
    yield sse({"type": "text-start", "id": part_id})
    yield sse({"type": "text-delta", "id": part_id, "delta": text})
    yield sse({"type": "text-end", "id": part_id})


async def _finish(reason: str) -> AsyncIterator[str]:
    yield sse({"type": "finish-step", "finishReason": reason})
    yield sse({"type": "finish", "finishReason": reason})
    yield "data: [DONE]\n\n"


def _ask_cost(reason: str, estimate: float) -> list[str]:
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    return [
        sse({"type": "tool-input-start", "toolCallId": call_id, "toolName": CONFIRM_TOOL}),
        sse(
            {
                "type": "tool-input-available",
                "toolCallId": call_id,
                "toolName": CONFIRM_TOOL,
                "input": {"reason": reason, "estimate_usd": round(estimate, 4)},
            }
        ),
    ]


def _log_call(settings: CompassSettings, usage: Usage, detail: str | None) -> float:
    return record_call(settings, usage, "chat", detail)


@dataclass
class _ModelStep:
    calls: list[ToolCall] = field(default_factory=list)
    cost: float = 0.0
    failed: bool = False


async def _model_step(
    settings: CompassSettings,
    model: ChatModel,
    messages: list[dict[str, Any]],
    topic: str | None,
    result: _ModelStep,
) -> AsyncIterator[str]:
    """One model call: stream its text, collect tool calls and cost into `result`."""
    text_id: str | None = None
    try:
        async for event in model.stream(messages, tool_schemas()):
            if isinstance(event, TextDelta):
                if text_id is None:
                    text_id = uuid.uuid4().hex
                    yield sse({"type": "text-start", "id": text_id})
                yield sse({"type": "text-delta", "id": text_id, "delta": event.text})
            elif isinstance(event, ToolCall):
                result.calls.append(event)
            elif isinstance(event, Usage):
                result.cost += await asyncio.to_thread(_log_call, settings, event, topic)
    except Exception:
        # The provider failed mid-reply. Say so in the thread instead of dropping the stream.
        logger.exception("model call failed")
        result.failed = True
    if text_id is not None:
        yield sse({"type": "text-end", "id": text_id})
    if result.failed:
        async for c in _text("The model call failed. Check the server log, then try again."):
            yield c


async def chat_stream(
    *,
    settings: CompassSettings,
    vault: AiVault,
    model_factory: Callable[[CompassSettings], ChatModel],
    messages: list[dict[str, Any]],
    screen: str | None,
    topic: str | None,
    today: date,
) -> AsyncIterator[str]:
    yield sse({"type": "start", "messageId": uuid.uuid4().hex})

    approval = cost_approval(messages)
    if approval is False:
        async for c in _text("Cancelled. Nothing was sent to the model."):
            yield c
        async for c in _finish("stop"):
            yield c
        return

    try:
        model = model_factory(settings)
    except AiNotConfiguredError as e:
        async for c in _text(str(e)):
            yield c
        async for c in _finish("stop"):
            yield c
        return

    topic_def = vault.definitions.topics.get(topic) if topic else None
    system = SYSTEM_PROMPT.format(
        context=describe_context(screen, topic, topic_def.name if topic_def else None)
    )
    model_messages = to_model_messages(messages, system)
    month_before = await asyncio.to_thread(month_spend, settings.compass_db_path)
    action_cost = 0.0

    for step in range(settings.compass_ai_max_steps):
        estimate = estimate_cost(settings, _chars(model_messages))
        limit = check_limits(
            settings, action_cost=action_cost, month_cost=month_before, next_call=estimate
        )
        if not limit.ok and approval is not True:
            for c in _ask_cost(limit.reason or "", estimate):
                yield c
            async for c in _finish("tool-calls"):
                yield c
            return

        if step > 0:
            yield sse({"type": "start-step"})
        result = _ModelStep()
        async for c in _model_step(settings, model, model_messages, topic, result):
            yield c
        action_cost += result.cost
        if result.failed or not result.calls:
            async for c in _finish("stop"):
                yield c
            return
        calls = result.calls

        model_messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in calls
                ],
            }
        )
        for call in calls:
            yield sse({"type": "tool-input-start", "toolCallId": call.id, "toolName": call.name})
            yield sse(
                {
                    "type": "tool-input-available",
                    "toolCallId": call.id,
                    "toolName": call.name,
                    "input": call.arguments,
                }
            )
            result = await asyncio.to_thread(run_tool, vault, call.name, call.arguments)
            yield sse({"type": "tool-output-available", "toolCallId": call.id, "output": result})
            model_messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
            )
        yield sse({"type": "finish-step", "finishReason": "tool-calls"})

    yield sse({"type": "start-step"})
    async for c in _text("I stopped after too many steps. Ask again to continue."):
        yield c
    async for c in _finish("stop"):
        yield c
