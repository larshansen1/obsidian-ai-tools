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
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from .ai_client import AiNotConfiguredError, ChatModel, TextDelta, ToolCall, Usage
from .ai_cost import check_limits, estimate_cost, month_spend, record_call
from .config import CompassSettings
from .source_ai import ActionBudget, SourceAi
from .vault_tools import AiVault, is_write_tool, run_tool, tool_schemas

logger = logging.getLogger(__name__)

CONFIRM_TOOL = "confirm_cost"
# Where a write tool's preview rides in its call input; never sent back to the model.
PREVIEW_KEY = "preview"
# The result a write gets when the user moved on without answering its card.
UNANSWERED = {"status": "cancelled", "message": "The user did not approve this change."}
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

link_notes and edit_topic change the vault. Their preview card waits for the user's \
click; the result you get back is the user's answer (written, refused or cancelled). \
Report that outcome in one line. Never ask them to approve again.

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


def _call_args(part: dict[str, Any]) -> str:
    args = dict(part.get("input") or {})
    args.pop(PREVIEW_KEY, None)
    return json.dumps(args)


def _assistant_entry(content: Any) -> dict[str, Any] | None:
    parts = content if isinstance(content, list) else []
    calls = [
        {
            "id": p["toolCallId"],
            "type": "function",
            "function": {"name": p["toolName"], "arguments": _call_args(p)},
        }
        for p in parts
        if p.get("type") == "tool-call" and p.get("toolName") != CONFIRM_TOOL
    ]
    text = _text_of(content)
    if not (text or calls):
        return None
    entry: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        entry["tool_calls"] = calls
    return entry


def _tool_entries(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "role": "tool",
            "tool_call_id": part["toolCallId"],
            "content": json.dumps(_result_value(part.get("output"))),
        }
        for part in content
        if part.get("type") == "tool-result" and part.get("toolName") != CONFIRM_TOOL
    ]


def _answer_unanswered(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every tool call a result. A write card the user never answered counts as
    cancelled; the model API refuses a call without a result.
    """
    out: list[dict[str, Any]] = []
    waiting: list[str] = []
    for msg in [*messages, None]:
        if msg is not None and msg["role"] == "tool":
            waiting = [i for i in waiting if i != msg["tool_call_id"]]
        else:
            out += [
                {"role": "tool", "tool_call_id": i, "content": json.dumps(UNANSWERED)}
                for i in waiting
            ]
            waiting = [c["id"] for c in (msg or {}).get("tool_calls", [])]
        if msg is not None:
            out.append(msg)
    return out


def to_model_messages(messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    """Thread (AI SDK shape) -> OpenAI chat messages. The cost question and write
    previews are dropped.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        role, content = msg.get("role"), msg.get("content")
        if role == "user":
            out.append({"role": "user", "content": _text_of(content)})
        elif role == "assistant":
            entry = _assistant_entry(content)
            if entry is not None:
                out.append(entry)
        elif role == "tool" and isinstance(content, list):
            out += _tool_entries(content)
    return _answer_unanswered(out)


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


def _input(call: ToolCall, value: dict[str, Any]) -> str:
    return sse(
        {
            "type": "tool-input-available",
            "toolCallId": call.id,
            "toolName": call.name,
            "input": value,
        }
    )


@dataclass
class _Paused:
    value: bool = False


async def _run_calls(
    vault: AiVault, calls: list[ToolCall], model_messages: list[dict[str, Any]], paused: _Paused
) -> AsyncIterator[str]:
    """Run each tool call. A write tool only plans: its preview goes out as the call's
    input and nothing runs until the user approves it in the card.
    """
    for call in calls:
        yield sse({"type": "tool-input-start", "toolCallId": call.id, "toolName": call.name})
        result = await asyncio.to_thread(run_tool, vault, call.name, call.arguments)
        if is_write_tool(call.name) and not (isinstance(result, dict) and "error" in result):
            yield _input(call, {**call.arguments, PREVIEW_KEY: result})
            paused.value = True
            continue
        yield _input(call, call.arguments)
        yield sse({"type": "tool-output-available", "toolCallId": call.id, "output": result})
        model_messages.append(
            {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
        )


async def chat_stream(
    *,
    settings: CompassSettings,
    vault: AiVault,
    model_factory: Callable[[CompassSettings], ChatModel],
    messages: list[dict[str, Any]],
    screen: str | None,
    topic: str | None,
    today: date,
    source_ai_factory: Callable[[CompassSettings, ActionBudget], SourceAi] | None = None,
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
    # Tools that pay for model calls (web search, source types) spend from the same action.
    budget = ActionBudget(settings, month_before, approved=approval is True)
    if source_ai_factory is not None:
        try:
            vault = replace(vault, source_ai=source_ai_factory(settings, budget))
        except AiNotConfiguredError:
            pass  # the tools fall back to rules and cache only

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
        budget.spent = action_cost
        paused = _Paused()
        async for c in _run_calls(vault, calls, model_messages, paused):
            yield c
        action_cost = budget.spent
        if paused.value:
            # A write waits for the user's click (C8); their answer comes back as its result.
            async for c in _finish("tool-calls"):
                yield c
            return
        yield sse({"type": "finish-step", "finishReason": "tool-calls"})

    yield sse({"type": "start-step"})
    async for c in _text("I stopped after too many steps. Ask again to continue."):
        yield c
    async for c in _finish("stop"):
        yield c
