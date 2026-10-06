"""Throwaway spike for issue #117: stream to assistant-ui from FastAPI.

Speaks the AI SDK v6 UI message stream (SSE) that
@assistant-ui/react-data-stream decodes. No LLM: replies are scripted so the
spike only tests the wire format, the tool card and the approval round trip.

Run: uv run uvicorn spikes.assistant_ui.server:app --host 127.0.0.1 --port 8100
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("spike")
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# Records every fake write so the spike can show none happen before a click.
WRITES: list[dict[str, Any]] = []


def sse(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk)}\n\n"


async def text_chunks(text: str) -> AsyncIterator[str]:
    part_id = uuid.uuid4().hex
    yield sse({"type": "text-start", "id": part_id})
    for word in text.split(" "):
        yield sse({"type": "text-delta", "id": part_id, "delta": word + " "})
        await asyncio.sleep(0.05)
    yield sse({"type": "text-end", "id": part_id})


def tool_call(name: str, args: dict[str, Any]) -> tuple[str, list[str]]:
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    return call_id, [
        sse({"type": "tool-input-start", "toolCallId": call_id, "toolName": name}),
        sse(
            {
                "type": "tool-input-available",
                "toolCallId": call_id,
                "toolName": name,
                "input": args,
            }
        ),
    ]


def last_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or []
        if isinstance(content, str):
            return content
        return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
    return ""


def pending_write_result(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the fake_write tool result if the last turn carries one."""
    if not messages or messages[-1].get("role") == "user":
        return None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            break
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        part: dict[str, Any]
        for part in content:
            if part.get("type") == "tool-result" and part.get("toolName") == "fake_write":
                return part
    return None


async def reply(messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    # "start" already opens a step in the decoder; a second "start-step" here
    # counts as an extra step and trips the runtime's maxSteps (default 2).
    yield sse({"type": "start", "messageId": uuid.uuid4().hex})

    approval = pending_write_result(messages)
    if approval is not None:
        # assistant-ui sends AI SDK v5 tool results: {"output": {"type": "json", "value": ...}}.
        result = (approval.get("output") or {}).get("value", {})
        if isinstance(result, dict) and result.get("approved"):
            WRITES.append({"toolCallId": approval.get("toolCallId")})
            logger.info("fake write executed after approval: %s", approval.get("toolCallId"))
            text = "Approved. The fake write ran (nothing touched the vault)."
        else:
            text = "Rejected. Nothing was written."
        async for c in text_chunks(text):
            yield c
        yield sse({"type": "finish-step", "finishReason": "stop"})
        yield sse({"type": "finish", "finishReason": "stop"})
        yield "data: [DONE]\n\n"
        return

    prompt = last_text(messages).lower()
    if "write" in prompt or "link" in prompt:
        async for c in text_chunks("I want to add a link. Approve it below."):
            yield c
        _, chunks = tool_call(
            "fake_write",
            {"path": "notes/example.md", "change": "add [[Agentic engineering]]"},
        )
        for c in chunks:
            yield c
        # No tool output: the call stays open until the user clicks.
        yield sse({"type": "finish-step", "finishReason": "tool-calls"})
        yield sse({"type": "finish", "finishReason": "tool-calls"})
        yield "data: [DONE]\n\n"
        return

    async for c in text_chunks("Here are the stats for that topic."):
        yield c
    call_id, chunks = tool_call("topic_stats", {"topic": "AI agents"})
    for c in chunks:
        yield c
    await asyncio.sleep(0.4)
    yield sse(
        {
            "type": "tool-output-available",
            "toolCallId": call_id,
            "output": {"topic": "AI agents", "notes": 214, "evergreens": 3, "momentum": 1.8},
        }
    )
    yield sse({"type": "finish-step", "finishReason": "tool-calls"})
    yield sse({"type": "start-step"})
    async for c in text_chunks("Momentum is high but few evergreens. Worth a synthesis note."):
        yield c
    yield sse({"type": "finish-step", "finishReason": "stop"})
    yield sse({"type": "finish", "finishReason": "stop"})
    yield "data: [DONE]\n\n"


@app.post("/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    messages = body.get("messages", [])
    logger.info("request messages: %s", json.dumps(messages)[:2000])
    return StreamingResponse(
        reply(messages),
        media_type="text/event-stream",
        headers={"x-vercel-ai-ui-message-stream": "v1", "Cache-Control": "no-cache"},
    )


@app.get("/writes")
async def writes() -> list[dict[str, Any]]:
    return WRITES
