"""Write and revise an evergreen draft with the model: nothing reaches the vault before save."""

import logging
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from vault_compass.ai_client import AiNotConfiguredError, ModelEvent, TextDelta, Usage
from vault_compass.ai_cost import estimate_cost
from vault_compass.app import NO_ANSWER_DETAIL, create_app
from vault_compass.claims import ClaimsView, EligibleNote, SplitSummary, claim_id, save_claims
from vault_compass.config import CompassSettings
from vault_compass.db import writable
from vault_compass.evergreen_ai import (
    ANSWER_SYSTEM,
    EMPTY,
    FAILED,
    REVISE_SYSTEM,
    WRITE_SYSTEM,
    answer_block,
    draft_prompt,
    join_sources,
    parse_note,
    split_sources,
)
from vault_compass.evergreen_draft import SOURCES_HEADING, claim_texts
from vault_compass.notes import refresh_notes
from vault_compass.topics import load_topics
from vault_compass.vault_writes import WriteError

TODAY = date(2026, 10, 8)

TOPICS_YAML = (
    "topics:\n"
    "  sleep:\n    name: Sleep\n    tags: [sleep, rest]\n"
    "min_notes: 1\ntrend_start: 2026-04-01\n"
    "ai_exclude_folders: [notes/reflections]\n"
)

SOURCES = f"{SOURCES_HEADING}\n\n- Naps help. ([[a]])"


def _note(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {path.stem}\ntags: [sleep]\n---\n{body}\n", encoding="utf-8")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _note(root, "notes/evergreen/Rest matters.md", "Evergreen.")
    _note(root, "notes/a.md", "Source A.")
    _note(root, "notes/deep/b.md", "Source B.")
    _note(root, "notes/reflections/r.md", "Private.")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    db = root / ".kai" / "compass.duckdb"
    refresh_notes(root, db, load_topics(root / ".kai/topics.yaml"))
    with writable(db) as con:
        save_claims(con, EligibleNote("notes/a.md", "a", 1), [("Naps help.", "note")])
        save_claims(con, EligibleNote("notes/deep/b.md", "b", 1), [("Light\n  wakes you.", "note")])
        save_claims(con, EligibleNote("notes/reflections/r.md", "r", 1), [("Secret.", "note")])
    return root


CLAIM_A = claim_id("notes/a.md", "Naps help.")
CLAIM_B = claim_id("notes/deep/b.md", "Light\n  wakes you.")
CLAIM_R = claim_id("notes/reflections/r.md", "Secret.")


class FakeModel:
    """Answers with `answer` (or raises `error`) and reports one call's usage."""

    def __init__(self, answer: str = "", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[list[dict[str, Any]]] = []

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ModelEvent]:
        assert tools == []
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        yield TextDelta(self.answer[:5])
        yield TextDelta(self.answer[5:])
        yield Usage(model="m/x", input_tokens=100, output_tokens=20, cost_usd=0.01)


def _files(vault: Path) -> dict[str, bytes]:
    return {p.relative_to(vault).as_posix(): p.read_bytes() for p in vault.rglob("*.md")}


def _settings(vault: Path, monkeypatch: pytest.MonkeyPatch, **extra: Any) -> CompassSettings:
    for key in ("COMPASS_DB_PATH", "COMPASS_TOPICS_PATH", "COMPASS_KAI_URL"):
        monkeypatch.delenv(key, raising=False)
    return CompassSettings(obsidian_vault_path=vault, openrouter_api_key="k", **extra)


def _client(
    settings: CompassSettings, model: FakeModel, seen: list[CompassSettings] | None = None
) -> TestClient:
    def factory(s: CompassSettings) -> FakeModel:
        if seen is not None:
            seen.append(s)
        return model

    return TestClient(create_app(settings, today=lambda: TODAY, model_factory=factory))


def _ai_calls(db: Path) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(db), read_only=True) as con:
        tables = {t for (t,) in con.execute("SHOW TABLES").fetchall()}
        if "usage_events" not in tables:
            return []
        return con.execute(
            "SELECT kind, name, detail, model, input_tokens, output_tokens, cost_usd "
            "FROM usage_events WHERE kind = 'ai_call'"
        ).fetchall()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "parts"),
    [
        ("", ("", "")),
        ("Just prose.\n", ("Just prose.", "")),
        (f"{SOURCES}\n", ("", SOURCES)),
        (f"\nIdea.\r\n\r\n{SOURCES}\r\n", ("Idea.", SOURCES)),
    ],
)
def test_split_sources(body: str, parts: tuple[str, str]) -> None:
    assert split_sources(body) == parts


@pytest.mark.parametrize(
    ("prose", "sources", "body"),
    [
        ("", "", ""),
        ("Idea.\n", "", "Idea.\n"),
        ("", SOURCES, f"{SOURCES}\n"),
        ("\nIdea.\n", f"{SOURCES}\n", f"Idea.\n\n{SOURCES}\n"),
    ],
)
def test_join_sources(prose: str, sources: str, body: str) -> None:
    assert join_sources(prose, sources) == body


@pytest.mark.parametrize(
    ("answer", "note"),
    [
        ("", None),
        ("  \n\n ", None),
        ("# Naps restore you\n\nShort sleep works.\n", ("Naps restore you", "Short sleep works.")),
        ("#  Naps restore you  ##\nShort.", ("Naps restore you", "Short.")),
        ("No heading here.\n\nMore.", ("", "No heading here.\n\nMore.")),
        ("```markdown\n# T\n\nBody.\n```", ("T", "Body.")),
        ("# T\n\nBody.\n\n## Claims this draws on\n\n- made up", ("T", "Body.")),
        ("# Only a title", ("Only a title", "")),
        ("#NoSpace\nBody.", ("", "#NoSpace\nBody.")),
    ],
)
def test_parse_note(answer: str, note: tuple[str, str] | None) -> None:
    assert parse_note(answer) == note


def test_first_draft_prompt_lists_the_claims() -> None:
    assert draft_prompt("Sleep", ["Naps help.", "Light wakes you."], "", "", None) == (
        WRITE_SYSTEM,
        "Topic: Sleep\n\nClaims:\n1. Naps help.\n2. Light wakes you.",
    )


def test_revision_prompt_carries_the_current_note_and_the_instruction() -> None:
    assert draft_prompt("Sleep", ["Naps help."], "Naps work", "Short.", "Shorter") == (
        REVISE_SYSTEM,
        "Topic: Sleep\n\nClaims:\n1. Naps help.\n\n"
        "Current note:\n# Naps work\n\nShort.\n\nInstruction: Shorter",
    )


def test_revision_prompt_without_a_title_sends_the_prose_alone() -> None:
    assert draft_prompt("Sleep", ["Naps help."], "", "Short.", "Add a title") == (
        REVISE_SYSTEM,
        "Topic: Sleep\n\nClaims:\n1. Naps help.\n\n"
        "Current note:\nShort.\n\nInstruction: Add a title",
    )


def test_claim_texts_keep_the_order_and_leave_out_excluded_folders(vault: Path) -> None:
    db = vault / ".kai/compass.duckdb"

    texts = claim_texts(db, [CLAIM_B, CLAIM_R, CLAIM_A, CLAIM_B], ["notes/reflections"])

    assert texts == ["Light wakes you.", "Naps help."]


def test_claim_texts_refuse_an_unknown_claim(vault: Path) -> None:
    with pytest.raises(WriteError) as e:
        claim_texts(vault / ".kai/compass.duckdb", ["nope"], [])
    assert str(e.value) == "Unknown claim: nope. Read the notes again."


# ---------------------------------------------------------------------------
# API: write, revise, refuse
# ---------------------------------------------------------------------------

ANSWER = "# Naps restore you\n\nA short nap brings back focus.\n"


def test_first_draft_writes_prose_above_the_sources_and_touches_no_file(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _files(vault)
    model = FakeModel(ANSWER)
    seen: list[CompassSettings] = []
    api = _client(_settings(vault, monkeypatch), model, seen)

    response = api.post(
        "/topics/sleep/evergreen-draft/write",
        json={"claim_ids": [CLAIM_A, CLAIM_R], "body": f"{SOURCES}\n"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "done",
        "message": None,
        "estimate_usd": None,
        "title": "Naps restore you",
        "body": f"A short nap brings back focus.\n\n{SOURCES}\n",
    }
    assert model.calls == [
        [
            {"role": "system", "content": WRITE_SYSTEM},
            {"role": "user", "content": "Topic: Sleep\n\nClaims:\n1. Naps help."},
        ]
    ]
    assert [(s.compass_ai_reasoning, s.compass_ai_max_output_tokens) for s in seen] == [
        (False, 6000)
    ]
    assert _ai_calls(vault / ".kai/compass.duckdb") == [
        ("ai_call", "evergreen_draft", "sleep", "m/x", 100, 20, 0.01)
    ]
    assert _files(vault) == before


def test_a_revision_applies_the_instruction_and_keeps_the_sources(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _files(vault)
    model = FakeModel("# Naps work\n\nShorter.\n")
    api = _client(_settings(vault, monkeypatch), model)

    response = api.post(
        "/topics/sleep/evergreen-draft/write",
        json={
            "claim_ids": [CLAIM_A],
            "title": "Naps restore you",
            "body": f"A short nap brings back focus.\n\n{SOURCES}\n",
            "instruction": "  Make it shorter  ",
        },
    )

    assert response.json() == {
        "status": "done",
        "message": None,
        "estimate_usd": None,
        "title": "Naps work",
        "body": f"Shorter.\n\n{SOURCES}\n",
    }
    assert model.calls[0][1] == {
        "role": "user",
        "content": "Topic: Sleep\n\nClaims:\n1. Naps help.\n\nCurrent note:\n"
        "# Naps restore you\n\nA short nap brings back focus.\n\nInstruction: Make it shorter",
    }
    assert model.calls[0][0] == {"role": "system", "content": REVISE_SYSTEM}
    assert _files(vault) == before


def test_an_answer_without_a_title_keeps_the_current_one(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _client(_settings(vault, monkeypatch), FakeModel("New text."))

    response = api.post(
        "/topics/sleep/evergreen-draft/write",
        json={"claim_ids": [CLAIM_A], "title": "Kept", "body": "Old.", "instruction": "Redo"},
    )

    assert (response.json()["title"], response.json()["body"]) == ("Kept", "New text.\n")


def _limit_case(
    vault: Path, monkeypatch: pytest.MonkeyPatch, limit_delta: float
) -> tuple[TestClient, float]:
    probe = _settings(vault, monkeypatch, compass_ai_draft_max_output_tokens=6000)
    tuned = probe.model_copy(update={"compass_ai_max_output_tokens": 6000})
    system, user = draft_prompt("Sleep", ["Naps help."], "", "", None)
    estimate = estimate_cost(tuned, len(system) + len(user))
    settings = _settings(vault, monkeypatch, compass_ai_action_limit_usd=estimate + limit_delta)
    return _client(settings, FakeModel(ANSWER)), estimate


def test_a_draft_exactly_at_the_action_limit_runs(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = _limit_case(vault, monkeypatch, 0.0)

    response = api.post("/topics/sleep/evergreen-draft/write", json={"claim_ids": [CLAIM_A]})

    assert response.json()["status"] == "done"


def test_a_draft_over_the_action_limit_asks_first_and_keeps_the_draft(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, estimate = _limit_case(vault, monkeypatch, -0.0001)
    payload = {"claim_ids": [CLAIM_A], "title": "T", "body": "Mine."}

    asked = api.post("/topics/sleep/evergreen-draft/write", json=payload)

    # (822 prompt chars / 4 * $3 + 6000 * $15) per million tokens.
    assert estimate == pytest.approx(0.0906165)
    assert asked.json() == {
        "status": "needs_approval",
        "message": "This action may cost up to $0.09, over the $0.09 per-action limit.",
        "estimate_usd": round(estimate, 4),
        "title": "T",
        "body": "Mine.",
    }
    assert _ai_calls(vault / ".kai/compass.duckdb") == []

    approved = api.post("/topics/sleep/evergreen-draft/write", json={**payload, "approved": True})

    assert approved.json()["status"] == "done"


def test_no_key_says_how_to_set_one(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(vault, monkeypatch)

    def factory(_s: CompassSettings) -> FakeModel:
        raise AiNotConfiguredError("Set OPENROUTER_API_KEY in .env to use the chat.")

    api = TestClient(create_app(settings, today=lambda: TODAY, model_factory=factory))

    response = api.post(
        "/topics/sleep/evergreen-draft/write",
        json={"claim_ids": [CLAIM_A], "title": "T", "body": "Mine."},
    )

    assert response.json() == {
        "status": "not_configured",
        "message": "Set OPENROUTER_API_KEY in .env to use the chat.",
        "estimate_usd": None,
        "title": "T",
        "body": "Mine.",
    }


def test_a_failed_call_keeps_the_draft_and_logs_why(
    vault: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    api = _client(_settings(vault, monkeypatch), FakeModel(error=RuntimeError("boom")))

    with caplog.at_level(logging.ERROR, logger="vault_compass.evergreen_ai"):
        response = api.post(
            "/topics/sleep/evergreen-draft/write",
            json={"claim_ids": [CLAIM_A], "title": "T", "body": "Mine.", "instruction": "x"},
        )

    assert response.json() == {
        "status": "failed",
        "message": FAILED,
        "estimate_usd": None,
        "title": "T",
        "body": "Mine.",
    }
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("ERROR", "evergreen draft model call failed")
    ]


def test_an_empty_answer_keeps_the_draft(
    vault: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    api = _client(_settings(vault, monkeypatch), FakeModel("   "))

    with caplog.at_level(logging.WARNING, logger="vault_compass.evergreen_ai"):
        response = api.post(
            "/topics/sleep/evergreen-draft/write",
            json={"claim_ids": [CLAIM_A], "title": "T", "body": "Mine."},
        )

    assert response.json() == {
        "status": "failed",
        "message": EMPTY,
        "estimate_usd": None,
        "title": "T",
        "body": "Mine.",
    }
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "evergreen draft answer was empty:    ")
    ]


@pytest.mark.parametrize(
    ("url", "body", "status", "detail"),
    [
        (
            "/topics/nope/evergreen-draft/write",
            {"claim_ids": [CLAIM_A]},
            404,
            "Unknown topic: nope",
        ),
        (
            "/topics/sleep/evergreen-draft/write",
            {"claim_ids": ["nope"]},
            400,
            "Unknown claim: nope. Read the notes again.",
        ),
    ],
)
def test_draft_write_errors(
    vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    body: dict[str, object],
    status: int,
    detail: str,
) -> None:
    model = FakeModel(ANSWER)
    api = _client(_settings(vault, monkeypatch), model)

    response = api.post(url, json=body)

    assert (response.status_code, response.json()) == (status, {"detail": detail})
    assert model.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"claim_ids": [CLAIM_A], "instruction": "   "},
        {"claim_ids": [CLAIM_A], "instruction": "x" * 2001},
        {"claim_ids": []},
    ],
)
def test_bad_requests_are_rejected_before_any_call(
    vault: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, object]
) -> None:
    model = FakeModel(ANSWER)
    api = _client(_settings(vault, monkeypatch), model)

    response = api.post("/topics/sleep/evergreen-draft/write", json=body)

    assert response.status_code == 422
    assert model.calls == []


def test_an_instruction_of_exactly_two_thousand_characters_is_taken(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _client(_settings(vault, monkeypatch), FakeModel(ANSWER))

    response = api.post(
        "/topics/sleep/evergreen-draft/write",
        json={"claim_ids": [CLAIM_A], "instruction": "x" * 2000},
    )

    assert response.json()["status"] == "done"


# ---------------------------------------------------------------------------
# Draft from the answer to the chosen question
# ---------------------------------------------------------------------------

SUMMARY = SplitSummary(
    bottom_line="Most notes say naps help.",
    agree=["Sleep debt hurts focus."],
    split=["Some say naps break night sleep.", "Others say length decides."],
    missing=[],
    read=[],
)

ANSWER_TEXT = (
    "Question: Do naps help?\n\n"
    "Bottom line: Most notes say naps help.\n\n"
    "Where they agree:\n- Sleep debt hurts focus.\n\n"
    "Where they split:\n- Some say naps break night sleep.\n- Others say length decides."
)


def _view(question: str | None, summary: SplitSummary | None) -> ClaimsView:
    return ClaimsView(
        topic="sleep",
        read_notes=2,
        pending_notes=0,
        claim_count=2,
        questions=[],
        question=question,
        supporting=[],
        pushing_back=[],
        unrelated=0,
        summary=summary,
        shared=[],
        stale=False,
        next_step="done",
    )


def test_answer_block_leaves_out_empty_sections() -> None:
    assert answer_block("Do naps help?", SUMMARY) == ANSWER_TEXT


def test_answer_block_with_only_a_bottom_line() -> None:
    bare = SplitSummary(bottom_line="Unclear.", agree=[], split=[], missing=["A trial."], read=[])

    assert answer_block("Q?", bare) == (
        "Question: Q?\n\nBottom line: Unclear.\n\nWhat is missing:\n- A trial."
    )


def test_a_first_draft_from_the_answer_uses_the_answer_prompt() -> None:
    assert draft_prompt("Sleep", ["Naps help."], "", "", None, ANSWER_TEXT) == (
        ANSWER_SYSTEM,
        f"Topic: Sleep\n\n{ANSWER_TEXT}\n\nClaims:\n1. Naps help.",
    )


def test_a_revision_keeps_the_answer_in_the_prompt() -> None:
    assert draft_prompt("Sleep", ["Naps help."], "T", "P.", "Shorter", ANSWER_TEXT) == (
        REVISE_SYSTEM,
        f"Topic: Sleep\n\n{ANSWER_TEXT}\n\nClaims:\n1. Naps help.\n\n"
        "Current note:\n# T\n\nP.\n\nInstruction: Shorter",
    )


def test_a_draft_from_the_answer_sends_the_question_and_summary(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeModel(ANSWER)
    api = _client(_settings(vault, monkeypatch), model)

    with patch(
        "vault_compass.app.build_claims_view", return_value=_view("Do naps help?", SUMMARY)
    ) as build:
        response = api.post(
            "/topics/sleep/evergreen-draft/write",
            json={"claim_ids": [CLAIM_A], "body": f"{SOURCES}\n", "answer": True},
        )

    assert response.json()["status"] == "done"
    assert model.calls == [
        [
            {"role": "system", "content": ANSWER_SYSTEM},
            {
                "role": "user",
                "content": f"Topic: Sleep\n\n{ANSWER_TEXT}\n\nClaims:\n1. Naps help.",
            },
        ]
    ]
    assert build.call_count == 1
    assert build.call_args.args[1:] == (load_topics(vault / ".kai/topics.yaml"), "sleep")


@pytest.mark.parametrize(
    ("question", "summary"), [(None, None), ("Do naps help?", None), (None, SUMMARY)]
)
def test_a_draft_from_the_answer_needs_a_sorted_answer(
    vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str | None,
    summary: SplitSummary | None,
) -> None:
    model = FakeModel(ANSWER)
    api = _client(_settings(vault, monkeypatch), model)

    with patch("vault_compass.app.build_claims_view", return_value=_view(question, summary)):
        response = api.post(
            "/topics/sleep/evergreen-draft/write", json={"claim_ids": [CLAIM_A], "answer": True}
        )

    assert (response.status_code, response.json()) == (409, {"detail": NO_ANSWER_DETAIL})
    assert model.calls == []


def test_a_draft_without_answer_does_not_read_the_claims_view(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _client(_settings(vault, monkeypatch), FakeModel(ANSWER))

    with patch("vault_compass.app.build_claims_view") as build:
        response = api.post("/topics/sleep/evergreen-draft/write", json={"claim_ids": [CLAIM_A]})

    assert response.json()["status"] == "done"
    build.assert_not_called()
