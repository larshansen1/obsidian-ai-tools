"""Tests for the AI foundation and chat endpoint (N2-N4, C2-C4, C6, C9)."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from vault_compass.ai_client import (
    AiNotConfiguredError,
    ModelEvent,
    OpenRouterModel,
    TextDelta,
    ToolCall,
    Usage,
)
from vault_compass.ai_cost import check_limits, estimate_cost, month_spend
from vault_compass.ai_policy import is_excluded
from vault_compass.app import create_app
from vault_compass.chat import cost_approval, describe_context, to_model_messages
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.topics import load_topics
from vault_compass.usage import log_usage
from vault_compass.vault_tools import AiVault, main, run_tool

TODAY = datetime(2026, 10, 6, tzinfo=UTC).date()
SECRET = "PRIVATE-REFLECTION-SENTENCE"

TOPICS_YAML = (
    "topics:\n"
    "  big:\n    name: Big Topic\n    tags: [x]\n"
    "  other:\n    name: Other\n    tags: [y]\n"
    "min_notes: 1\ntrend_start: 2026-04-01\n"
    "ai_exclude_folders: [notes/reflections]\n"
)


class FakeModel:
    """Plays back one scripted list of events per model call and records each request."""

    def __init__(self, script: list[list[ModelEvent]]) -> None:
        self.script = list(script)
        self.requests: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ModelEvent]:
        self.requests.append((json.loads(json.dumps(messages)), tools))
        for event in self.script.pop(0):
            yield event


def _usage(cost: float | None = 0.01) -> Usage:
    return Usage(model="m/x", input_tokens=100, output_tokens=20, cost_usd=cost)


def _write(vault: Path, rel: str, body: str, tags: str = "[x]") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {path.stem}\ncreated: 2026-09-01\ntags: {tags}\n---\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    _write(root, "notes/agents.md", "Agents plan work.\nAgents use tools often.")
    _write(root, "notes/other.md", "Unrelated gardening text.", tags="[y]")
    _write(root, "notes/reflections/diary.md", f"Agents. {SECRET}")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(root))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    for key in ("COMPASS_DB_PATH", "COMPASS_TOPICS_PATH", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    refresh_notes(
        root, root / ".kai" / "compass.duckdb", load_topics(root / ".kai" / "topics.yaml")
    )
    return root


def _client(model: FakeModel) -> TestClient:
    return TestClient(
        create_app(CompassSettings(), today=lambda: TODAY, model_factory=lambda _s: model)
    )


def _events(response: Any) -> list[dict[str, Any] | str]:
    out: list[dict[str, Any] | str] = []
    for line in response.text.split("\n\n"):
        if not line:
            continue
        assert line.startswith("data: ")
        payload = line.removeprefix("data: ")
        out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _post(client: TestClient, messages: list[dict[str, Any]], **extra: Any) -> Any:
    return client.post("/chat", json={"messages": messages, **extra})


# --- N4: folder exclusion ---------------------------------------------------


@pytest.mark.parametrize(
    ("path", "excluded"),
    [
        ("notes/reflections/a.md", True),
        ("notes/reflections/deep/a.md", True),
        ("notes/reflections", True),
        ("notes/reflectionsX/a.md", False),
        ("other/notes/reflections/a.md", False),
        ("notes/a.md", False),
    ],
)
def test_is_excluded_matches_whole_folders_only(path: str, excluded: bool) -> None:
    assert is_excluded(path, ["notes/reflections"]) is excluded


def test_is_excluded_with_no_folders_excludes_nothing() -> None:
    assert is_excluded("notes/reflections/a.md", []) is False


def test_excluded_note_never_reaches_the_model(vault: Path) -> None:
    model = FakeModel(
        [
            [
                ToolCall("c1", "search_notes", {"query": "agents"}),
                ToolCall("c2", "read_note", {"path": "notes/reflections/diary.md"}),
                _usage(),
            ],
            [TextDelta("Done."), _usage()],
        ]
    )

    response = _post(_client(model), [_user("what about agents?")])

    assert response.status_code == 200
    assert len(model.requests) == 2
    sent = json.dumps(model.requests)
    assert SECRET not in sent
    assert "notes/reflections/diary.md" in sent  # only as the path the model asked for
    second_tool_messages = [m for m in model.requests[1][0] if m["role"] == "tool"]
    assert [json.loads(m["content"]) for m in second_tool_messages] == [
        [
            {
                "path": "notes/agents.md",
                "title": "agents",
                "created": "2026-09-01",
                "snippet": "Agents plan work.",
            }
        ],
        {"error": "notes/reflections/diary.md is excluded from AI use."},
    ]


def test_search_never_returns_excluded_notes(vault: Path) -> None:
    ai = AiVault.from_settings(CompassSettings())

    assert [r["path"] for r in ai.search_notes("agents")] == ["notes/agents.md"]


# --- tools (C4) -------------------------------------------------------------


def test_search_ranks_title_and_tags_above_body(vault: Path) -> None:
    _write(vault, "notes/zeta.md", "mentions rocket once")
    _write(vault, "notes/rocket.md", "nothing")
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )
    ai = AiVault.from_settings(CompassSettings())

    assert [r["path"] for r in ai.search_notes("rocket")] == ["notes/rocket.md", "notes/zeta.md"]


def test_search_limits_to_one_topic(vault: Path) -> None:
    ai = AiVault.from_settings(CompassSettings())

    assert [r["path"] for r in ai.search_notes("text gardening", topic="other")] == [
        "notes/other.md"
    ]
    assert ai.search_notes("gardening", topic="big") == []


def test_search_limit_is_eight_by_default(vault: Path) -> None:
    for i in range(10):
        _write(vault, f"notes/many{i}.md", "banana")
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )
    ai = AiVault.from_settings(CompassSettings())

    assert len(ai.search_notes("banana")) == 8


def test_tool_errors_come_back_as_data(vault: Path) -> None:
    ai = AiVault.from_settings(CompassSettings())

    assert run_tool(ai, "search_notes", {"query": "a"}) == {
        "error": "The query has no searchable words."
    }
    assert run_tool(ai, "search_notes", {"query": "agents", "topic": "nope"}) == {
        "error": "Unknown topic: nope"
    }
    assert run_tool(ai, "read_note", {"path": "../etc/passwd"}) == {
        "error": "No such note: ../etc/passwd"
    }
    assert run_tool(ai, "topic_stats", {"topic": "nope"}) == {"error": "Unknown topic: nope"}
    assert run_tool(ai, "topic_stats", {"topic": "big", "window": "7"}) == {
        "error": "window must be 30, 90 or all"
    }
    assert run_tool(ai, "no_such_tool", {}) == {"error": "Unknown tool: no_such_tool"}


def test_read_note_returns_body_without_front_matter(vault: Path) -> None:
    ai = AiVault.from_settings(CompassSettings())

    assert run_tool(ai, "read_note", {"path": "notes/agents.md"}) == {
        "path": "notes/agents.md",
        "title": "agents",
        "created": "2026-09-01",
        "tags": ["x"],
        "text": "Agents plan work.\nAgents use tools often.\n",
        "truncated": False,
    }


def test_read_note_truncates_at_6000_characters(vault: Path) -> None:
    _write(vault, "notes/long.md", "a" * 6001)
    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )
    ai = AiVault.from_settings(CompassSettings())

    note = run_tool(ai, "read_note", {"path": "notes/long.md"})

    assert len(note["text"]) == 6000
    assert note["truncated"] is True


def test_topic_stats_matches_the_topic_map(vault: Path) -> None:
    client = _client(FakeModel([]))
    ai = AiVault.from_settings(CompassSettings())

    stats = ai.topic_stats("big", "all", today=TODAY)

    expected = next(
        t for t in client.get("/topic-map?window=all").json()["topics"] if t["id"] == "big"
    )
    assert stats == expected


def test_tools_work_from_a_script(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["search_notes", '{"query": "gardening"}'])

    assert json.loads(capsys.readouterr().out) == [
        {
            "path": "notes/other.md",
            "title": "other",
            "created": "2026-09-01",
            "snippet": "Unrelated gardening text.",
        }
    ]


def test_script_usage_error() -> None:
    with pytest.raises(SystemExit, match="usage: vault_tools"):
        main(["only-one"])


def test_tools_need_a_scan_first(tmp_path: Path, vault: Path) -> None:
    (vault / ".kai" / "compass.duckdb").unlink()
    ai = AiVault.from_settings(CompassSettings())

    assert run_tool(ai, "search_notes", {"query": "agents"}) == {
        "error": "No topic data yet. Run `compass scan` first."
    }
    assert run_tool(ai, "topic_stats", {"topic": "big"}) == {
        "error": "No topic data yet. Run `compass scan` first."
    }


# --- stream (C2, C3, C6) ----------------------------------------------------


def test_stream_text_reply_exact_chunks(vault: Path) -> None:
    model = FakeModel([[TextDelta("Hello "), TextDelta("world"), _usage()]])

    events = _events(_post(_client(model), [_user("hi")]))

    ids = {e["id"] for e in events if isinstance(e, dict) and "id" in e}
    assert len(ids) == 1
    part_id = ids.pop()
    assert events[0]["type"] == "start"  # type: ignore[index]
    assert events[1:] == [
        {"type": "text-start", "id": part_id},
        {"type": "text-delta", "id": part_id, "delta": "Hello "},
        {"type": "text-delta", "id": part_id, "delta": "world"},
        {"type": "text-end", "id": part_id},
        {"type": "finish-step", "finishReason": "stop"},
        {"type": "finish", "finishReason": "stop"},
        "[DONE]",
    ]


def test_stream_headers(vault: Path) -> None:
    response = _post(_client(FakeModel([[TextDelta("x"), _usage()]])), [_user("hi")])

    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["content-type"].startswith("text/event-stream")


def test_tool_call_streams_input_and_output_then_continues(vault: Path) -> None:
    model = FakeModel(
        [
            [ToolCall("c1", "search_notes", {"query": "gardening"}), _usage()],
            [TextDelta("See [[notes/other.md]]."), _usage()],
        ]
    )

    events = _events(_post(_client(model), [_user("gardening?")]))

    types = [e["type"] if isinstance(e, dict) else e for e in events]
    assert types == [
        "start",
        "tool-input-start",
        "tool-input-available",
        "tool-output-available",
        "finish-step",
        "start-step",
        "text-start",
        "text-delta",
        "text-end",
        "finish-step",
        "finish",
        "[DONE]",
    ]
    assert events[1] == {"type": "tool-input-start", "toolCallId": "c1", "toolName": "search_notes"}
    assert events[2] == {
        "type": "tool-input-available",
        "toolCallId": "c1",
        "toolName": "search_notes",
        "input": {"query": "gardening"},
    }
    assert events[3] == {
        "type": "tool-output-available",
        "toolCallId": "c1",
        "output": [
            {
                "path": "notes/other.md",
                "title": "other",
                "created": "2026-09-01",
                "snippet": "Unrelated gardening text.",
            }
        ],
    }
    assert events[4] == {"type": "finish-step", "finishReason": "tool-calls"}


def test_system_prompt_carries_screen_and_topic(vault: Path) -> None:
    model = FakeModel([[TextDelta("ok"), _usage()]])

    _post(
        _client(model),
        [_user("what am I missing?")],
        screen="topic_page",
        topic="big",
    )

    system = model.requests[0][0][0]
    assert system["role"] == "system"
    assert (
        'The user is on the topic_page screen. They are looking at the topic "Big Topic" '
        "(topic id: big). Questions without a topic name are about this topic."
    ) in system["content"]
    assert model.requests[0][0][1] == {"role": "user", "content": "what am I missing?"}
    assert [t["function"]["name"] for t in model.requests[0][1]] == [
        "search_notes",
        "read_note",
        "topic_stats",
    ]


def test_system_prompt_asks_for_citations(vault: Path) -> None:
    model = FakeModel([[TextDelta("ok"), _usage()]])

    _post(_client(model), [_user("hi")])

    assert "[[notes/evergreen/example.md]]" in model.requests[0][0][0]["content"]


def test_describe_context_variants() -> None:
    assert describe_context(None, None, None) == "The user's current screen is unknown."
    assert describe_context("topic_map", None, None) == "The user is on the topic_map screen."
    assert describe_context(None, "t", None) == (
        'The user is on the unknown screen. They are looking at the topic "t" (topic id: t). '
        "Questions without a topic name are about this topic."
    )


def test_thread_is_converted_for_the_model() -> None:
    thread = [
        _user("q1"),
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "looking"},
                {
                    "type": "tool-call",
                    "toolCallId": "c1",
                    "toolName": "read_note",
                    "input": {"path": "p"},
                },
                {"type": "tool-call", "toolCallId": "c9", "toolName": "confirm_cost", "input": {}},
            ],
        },
        {
            "role": "tool",
            "content": [
                {
                    "type": "tool-result",
                    "toolCallId": "c1",
                    "toolName": "read_note",
                    "output": {"type": "json", "value": {"text": "t"}},
                },
                {
                    "type": "tool-result",
                    "toolCallId": "c9",
                    "toolName": "confirm_cost",
                    "output": {"type": "json", "value": {"approved": True}},
                },
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool-call", "toolCallId": "c8", "toolName": "confirm_cost"}],
        },
    ]

    assert to_model_messages(thread, "SYS") == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q1"},
        {
            "role": "assistant",
            "content": "looking",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_note", "arguments": '{"path": "p"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"text": "t"}'},
    ]


def test_too_many_steps_stops_with_a_message(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_AI_MAX_STEPS", "2")
    model = FakeModel(
        [
            [ToolCall("c1", "search_notes", {"query": "agents"}), _usage()],
            [ToolCall("c2", "search_notes", {"query": "agents"}), _usage()],
        ]
    )

    events = _events(_post(_client(model), [_user("loop")]))

    assert len(model.requests) == 2
    assert events[-7]["type"] == "start-step"  # type: ignore[index]
    texts = [e["delta"] for e in events if isinstance(e, dict) and e["type"] == "text-delta"]
    assert texts == ["I stopped after too many steps. Ask again to continue."]


def test_missing_api_key_explains_instead_of_failing(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    client = TestClient(create_app(CompassSettings(_env_file=None), today=lambda: TODAY))  # type: ignore[call-arg]

    events = _events(_post(client, [_user("hi")]))

    texts = [e["delta"] for e in events if isinstance(e, dict) and e["type"] == "text-delta"]
    assert texts == ["Set OPENROUTER_API_KEY in .env to use the chat."]


# --- N3: costs --------------------------------------------------------------


def test_each_model_call_is_logged_with_cost(vault: Path) -> None:
    model = FakeModel(
        [
            [ToolCall("c1", "search_notes", {"query": "agents"}), _usage(0.02)],
            [TextDelta("ok"), _usage(None)],
        ]
    )

    _post(_client(model), [_user("hi")], topic="big")

    con = duckdb.connect(str(vault / ".kai" / "compass.duckdb"), read_only=True)
    rows = con.execute(
        "SELECT kind, name, detail, model, input_tokens, output_tokens, cost_usd "
        "FROM usage_events ORDER BY ts"
    ).fetchall()
    assert rows == [
        ("ai_call", "chat", "big", "m/x", 100, 20, 0.02),
        # No cost from the provider: estimated from the configured token prices.
        ("ai_call", "chat", "big", "m/x", 100, 20, (100 * 3.0 + 20 * 15.0) / 1_000_000),
    ]


def test_month_spend_sums_only_this_months_ai_calls(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    log_usage(db, "ai_call", "chat", cost_usd=1.5, now=datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
    log_usage(db, "ai_call", "chat", cost_usd=0.25, now=datetime(2026, 10, 31, 23, 59, tzinfo=UTC))
    log_usage(db, "ai_call", "chat", cost_usd=9, now=datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    log_usage(db, "ai_call", "chat", cost_usd=9, now=datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    log_usage(db, "action", "x", cost_usd=9, now=datetime(2026, 10, 5, tzinfo=UTC))

    assert month_spend(db, datetime(2026, 10, 15, tzinfo=UTC)) == 1.75


def test_month_spend_in_december_rolls_the_year(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    log_usage(db, "ai_call", "chat", cost_usd=2, now=datetime(2026, 12, 31, 23, 0, tzinfo=UTC))
    log_usage(db, "ai_call", "chat", cost_usd=5, now=datetime(2027, 1, 1, 0, 0, tzinfo=UTC))

    assert month_spend(db, datetime(2026, 12, 2, tzinfo=UTC)) == 2


def test_month_spend_is_zero_without_data(tmp_path: Path) -> None:
    assert month_spend(tmp_path / "missing.duckdb") == 0.0
    other = tmp_path / "other.duckdb"
    duckdb.connect(str(other)).close()
    assert month_spend(other) == 0.0


def test_estimate_cost_uses_configured_prices(vault: Path) -> None:
    settings = CompassSettings()

    # 4000 chars = 1000 input tokens at $3/M, plus 1500 output tokens at $15/M.
    assert estimate_cost(settings, 4000) == pytest.approx((1000 * 3 + 1500 * 15) / 1_000_000)


def test_check_limits_boundaries(vault: Path) -> None:
    settings = CompassSettings()  # action 0.25, month 10.0

    assert check_limits(settings, action_cost=0.0, month_cost=0.0, next_call=0.25).ok is True
    over_action = check_limits(settings, action_cost=0.1, month_cost=0.0, next_call=0.1500001)
    assert over_action.ok is False
    assert over_action.reason == (
        "This action may cost up to $0.25, over the $0.25 per-action limit."
    )
    assert check_limits(settings, action_cost=0.0, month_cost=9.75, next_call=0.25).ok is True
    over_month = check_limits(settings, action_cost=0.0, month_cost=9.76, next_call=0.25)
    assert over_month.reason == (
        "This would bring this month's AI spend to $10.01, over the $10.00 monthly limit."
    )


def _ask(model: FakeModel) -> list[dict[str, Any] | str]:
    return _events(_post(_client(model), [_user("hi")]))


def test_over_the_action_limit_asks_before_running(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.001")
    model = FakeModel([[TextDelta("never"), _usage()]])

    events = _ask(model)

    assert model.requests == []
    call_id = events[1]["toolCallId"]  # type: ignore[index]
    assert events[1:] == [
        {"type": "tool-input-start", "toolCallId": call_id, "toolName": "confirm_cost"},
        {
            "type": "tool-input-available",
            "toolCallId": call_id,
            "toolName": "confirm_cost",
            "input": {
                "reason": events[2]["input"]["reason"],  # type: ignore[index]
                "estimate_usd": events[2]["input"]["estimate_usd"],  # type: ignore[index]
            },
        },
        {"type": "finish-step", "finishReason": "tool-calls"},
        {"type": "finish", "finishReason": "tool-calls"},
        "[DONE]",
    ]
    assert events[2]["input"]["reason"].endswith("over the $0.0010 per-action limit.")  # type: ignore[index]


def test_over_the_monthly_limit_asks_before_running(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_MONTHLY_LIMIT_USD", "1.0")
    log_usage(vault / ".kai" / "compass.duckdb", "ai_call", "chat", cost_usd=0.999)
    model = FakeModel([[TextDelta("never"), _usage()]])

    events = _ask(model)

    assert model.requests == []
    assert events[2]["toolName"] == "confirm_cost"  # type: ignore[index]
    assert "over the $1.00 monthly limit." in events[2]["input"]["reason"]  # type: ignore[index]


def _approval_thread(approved: bool) -> list[dict[str, Any]]:
    return [
        _user("hi"),
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool-call",
                    "toolCallId": "cc",
                    "toolName": "confirm_cost",
                    "input": {"reason": "r", "estimate_usd": 1},
                }
            ],
        },
        {
            "role": "tool",
            "content": [
                {
                    "type": "tool-result",
                    "toolCallId": "cc",
                    "toolName": "confirm_cost",
                    "output": {"type": "json", "value": {"approved": approved}},
                }
            ],
        },
    ]


def test_approved_action_runs_despite_the_limit(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.001")
    model = FakeModel([[TextDelta("ran"), _usage()]])

    events = _events(_post(_client(model), _approval_thread(True)))

    assert [m for m in model.requests[0][0] if m["role"] != "system"] == [
        {"role": "user", "content": "hi"}
    ]
    assert [e["delta"] for e in events if isinstance(e, dict) and e["type"] == "text-delta"] == [
        "ran"
    ]


def test_rejected_action_sends_nothing(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.001")
    model = FakeModel([])

    events = _events(_post(_client(model), _approval_thread(False)))

    assert model.requests == []
    assert [e["delta"] for e in events if isinstance(e, dict) and e["type"] == "text-delta"] == [
        "Cancelled. Nothing was sent to the model."
    ]


def test_cost_approval_reads_only_the_latest_turn() -> None:
    assert cost_approval(_approval_thread(True)) is True
    assert cost_approval(_approval_thread(False)) is False
    assert cost_approval([*_approval_thread(True), _user("again")]) is None
    assert cost_approval([_user("hi")]) is None
    malformed = _approval_thread(True)
    malformed[2]["content"][0]["output"] = "yes"
    assert cost_approval(malformed) is False


def test_status_shows_month_spend_and_limits(vault: Path) -> None:
    log_usage(vault / ".kai" / "compass.duckdb", "ai_call", "chat", cost_usd=0.5)
    client = _client(FakeModel([]))

    assert client.get("/ai/status").json() == {
        "configured": True,
        "model": "anthropic/claude-sonnet-4",
        "vault_name": "vault",
        "month_spend_usd": 0.5,
        "monthly_limit_usd": 10.0,
        "action_limit_usd": 0.25,
    }


def test_model_comes_from_config(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "other/model")

    assert _client(FakeModel([])).get("/ai/status").json()["model"] == "other/model"


# --- C9: history ------------------------------------------------------------


def test_history_is_kept_per_topic(vault: Path) -> None:
    client = _client(FakeModel([]))

    assert client.get("/chat/history?topic=big").json() == {"messages": None}
    assert client.put("/chat/history?topic=big", json={"messages": {"a": 1}}).status_code == 204
    assert client.put("/chat/history", json={"messages": {"g": 2}}).status_code == 204
    assert client.put("/chat/history?topic=big", json={"messages": {"a": 3}}).status_code == 204

    assert client.get("/chat/history?topic=big").json() == {"messages": {"a": 3}}
    assert client.get("/chat/history?topic=other").json() == {"messages": None}
    assert client.get("/chat/history").json() == {"messages": {"g": 2}}


def test_history_for_unknown_topic_is_404(vault: Path) -> None:
    client = _client(FakeModel([]))

    assert client.get("/chat/history?topic=nope").status_code == 404
    assert client.put("/chat/history?topic=nope", json={"messages": {}}).status_code == 404


def test_history_survives_a_notes_refresh(vault: Path) -> None:
    client = _client(FakeModel([]))
    client.put("/chat/history?topic=big", json={"messages": {"a": 1}})

    refresh_notes(
        vault, vault / ".kai" / "compass.duckdb", load_topics(vault / ".kai" / "topics.yaml")
    )

    assert client.get("/chat/history?topic=big").json() == {"messages": {"a": 1}}


# --- N2: OpenRouter client --------------------------------------------------


def _chunk(
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    usage: Any = None,
) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choices = [SimpleNamespace(delta=delta)] if usage is None else []
    return SimpleNamespace(choices=choices, usage=usage)


def _tc(index: int, id_: str | None, name: str | None, args: str) -> Any:
    return SimpleNamespace(index=index, id=id_, function=SimpleNamespace(name=name, arguments=args))


def _collect(stream: AsyncIterator[ModelEvent]) -> list[ModelEvent]:
    async def run() -> list[ModelEvent]:
        return [e async for e in stream]

    return asyncio.run(run())


async def _aiter(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


def test_openrouter_model_streams_text_tools_and_cost(vault: Path) -> None:
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, model_extra={"cost": 0.003})
    chunks = [
        _chunk("Hel"),
        _chunk("lo"),
        _chunk(tool_calls=[_tc(0, "call_a", "read_note", '{"pa')]),
        _chunk(tool_calls=[_tc(0, None, None, 'th": "p"}')]),
        _chunk(tool_calls=[_tc(1, "call_b", "topic_stats", "not json")]),
        _chunk(usage=usage),
    ]
    create = AsyncMock(return_value=_aiter(chunks))
    client = MagicMock()
    client.chat.completions.create = create
    with patch("vault_compass.ai_client.AsyncOpenAI", return_value=client) as ctor:
        model = OpenRouterModel(CompassSettings())
        events = _collect(model.stream([{"role": "user", "content": "q"}], [{"t": 1}]))

    ctor.assert_called_once_with(api_key="k", base_url="https://openrouter.ai/api/v1")
    create.assert_called_once_with(
        model="anthropic/claude-sonnet-4",
        messages=[{"role": "user", "content": "q"}],
        max_tokens=1500,
        stream=True,
        stream_options={"include_usage": True},
        extra_body={"usage": {"include": True}},
        tools=[{"t": 1}],
    )
    assert events == [
        TextDelta("Hel"),
        TextDelta("lo"),
        ToolCall("call_a", "read_note", {"path": "p"}),
        ToolCall("call_b", "topic_stats", {}),
        Usage("anthropic/claude-sonnet-4", 11, 7, 0.003),
    ]


def test_openrouter_model_without_cost_or_tools(vault: Path) -> None:
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, model_extra=None)
    create = AsyncMock(return_value=_aiter([_chunk(usage=usage)]))
    client = MagicMock()
    client.chat.completions.create = create
    with patch("vault_compass.ai_client.AsyncOpenAI", return_value=client):
        model = OpenRouterModel(CompassSettings())
        events = _collect(model.stream([], []))

    assert "tools" not in create.call_args.kwargs
    assert events == [Usage("anthropic/claude-sonnet-4", 1, 2, None)]


def test_openrouter_model_needs_a_key(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")

    with pytest.raises(AiNotConfiguredError, match="Set OPENROUTER_API_KEY in .env"):
        OpenRouterModel(CompassSettings(_env_file=None))  # type: ignore[call-arg]


def test_limit_messages_keep_precision_for_tiny_amounts(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.0099")
    settings = CompassSettings()

    tiny = check_limits(settings, action_cost=0.0, month_cost=0.0, next_call=0.01)

    assert tiny.reason == "This action may cost up to $0.01, over the $0.0099 per-action limit."


def test_search_skips_a_note_that_cannot_be_read(vault: Path) -> None:
    (vault / "notes" / "agents.md").unlink()
    ai = AiVault.from_settings(CompassSettings())

    assert ai.search_notes("agents") == []
