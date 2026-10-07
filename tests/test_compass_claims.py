"""Tests for claims across sources and where they agree (T3, T4, Q6, Q7, N4)."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from vault_compass.ai_client import ModelEvent, TextDelta, Usage
from vault_compass.app import create_app
from vault_compass.claims import claim_id
from vault_compass.claims_run import (
    key_claims,
    parse_extracted,
    parse_json_object,
    parse_questions,
    parse_shared,
    parse_stances,
)
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.topics import load_topics
from vault_compass.usage import log_usage
from vault_compass.vault_tools import AiVault, run_tool

TODAY = datetime(2026, 10, 6, tzinfo=UTC).date()
SECRET = "PRIVATE-REFLECTION-SENTENCE"
TOPICS_YAML = (
    "topics:\n"
    "  big:\n    name: Big Topic\n    tags: [x]\n"
    "min_notes: 1\ntrend_start: 2026-04-01\n"
    "ai_exclude_folders: [notes/reflections]\n"
)
A, B, KEY = "notes/a.md", "notes/b.md", "notes/key.md"
EVERGREEN = "notes/evergreen/ev.md"
DIARY = "notes/reflections/diary.md"


class ScriptedModel:
    """Answers each call with the next scripted JSON object and records the prompts."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ModelEvent]:
        assert tools == []
        self.prompts.append(messages[1]["content"])
        answer = self.answers.pop(0)
        yield TextDelta(answer if isinstance(answer, str) else json.dumps(answer))
        yield Usage(model="m/x", input_tokens=100, output_tokens=20, cost_usd=0.01)


def _write(vault: Path, rel: str, body: str, links: str = "") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {path.stem}\ncreated: 2026-09-01\ntags: [x]\n---\n{body}\n{links}\n",
        encoding="utf-8",
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    _write(root, A, "Agents plan work.", links="See [[ev]].")
    _write(root, B, "Agents use tools.")
    _write(root, KEY, "Intro.\n\n## Key Claims\n- Tools help.\n- Plans fail.\n\n## Other\n- no\n")
    _write(root, EVERGREEN, "Agents need plans and tools.")
    _write(root, DIARY, f"Agents. {SECRET}")
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


def _db(vault: Path) -> Path:
    return vault / ".kai" / "compass.duckdb"


def _client(model: ScriptedModel | None) -> TestClient:
    def factory(_settings: CompassSettings) -> ScriptedModel:
        assert model is not None
        return model

    return TestClient(create_app(CompassSettings(), today=lambda: TODAY, model_factory=factory))


def _extraction() -> dict[str, Any]:
    return {
        "claims": [
            {"note": A, "text": "Agents plan before acting."},
            {"note": B, "text": "Agents rely on tools."},
            {"note": "notes/not-in-batch.md", "text": "dropped"},
        ]
    }


def _ids() -> dict[str, str]:
    return {
        "a": claim_id(A, "Agents plan before acting."),
        "b": claim_id(B, "Agents rely on tools."),
        "k1": claim_id(KEY, "Tools help."),
        "k2": claim_id(KEY, "Plans fail."),
    }


def _full_script() -> list[Any]:
    ids = _ids()
    return [
        _extraction(),
        {"questions": ["Do agents need plans?", "Do tools matter?", "Q3", "Q4 extra"]},
        {
            "shared": [
                {
                    "text": "Agents need tools.",
                    "claim_ids": [ids["b"], ids["k1"], "unknown-id"],
                    "evergreen": EVERGREEN,
                },
                {"text": "single note only", "claim_ids": [ids["a"]], "evergreen": None},
            ]
        },
    ]


# --- pure parsing ---------------------------------------------------------------


def test_key_claims_reads_bullets_under_the_heading_only() -> None:
    body = "x\n## Key Claims\n- one\n* two\n1. three\nnot a bullet\n\n## Next\n- skipped\n"
    assert key_claims(body) == ["one", "two", "three"]


def test_key_claims_is_empty_without_the_heading() -> None:
    assert key_claims("# Title\n- a bullet\n") == []


def test_key_claims_heading_is_case_insensitive_and_runs_to_the_end() -> None:
    assert key_claims("### key claims\n- last\n") == ["last"]


def test_key_claims_cuts_long_bullets_at_300_characters() -> None:
    assert key_claims("## Key Claims\n- " + "x" * 301) == ["x" * 300]


def test_parse_json_object_ignores_a_code_fence() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.parametrize("text", ["no json", "[1, 2]", "}{", '{"a": }'])
def test_parse_json_object_rejects_bad_answers(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_json_object(text)


def test_parse_extracted_drops_other_notes_blanks_and_extra_claims() -> None:
    data = {
        "claims": [
            "junk",
            {"note": A, "text": "  one  "},
            {"note": A, "text": ""},
            {"note": A, "text": 5},
            {"note": "other.md", "text": "x"},
            *({"note": B, "text": f"c{i}"} for i in range(7)),
        ]
    }
    assert parse_extracted(data, {A, B}) == {A: ["one"], B: ["c0", "c1", "c2", "c3", "c4"]}


def test_parse_questions_keeps_three_unique_non_blank() -> None:
    data = {"questions": ["Q1", " Q1 ", "", 3, "Q2", "Q3", "Q4"]}
    assert parse_questions(data) == ["Q1", "Q2", "Q3"]


def test_parse_shared_needs_two_notes_and_known_ids_and_paths() -> None:
    from vault_compass.claims import ClaimItem

    claims = [
        ClaimItem(id="1", text="t", path=A, title="a"),
        ClaimItem(id="2", text="t", path=B, title="b"),
        ClaimItem(id="3", text="t", path=B, title="b"),
    ]
    data = {
        "shared": [
            {"text": "ok", "claim_ids": ["1", "2", "2", "zz"], "evergreen": "ev.md"},
            {"text": "same note", "claim_ids": ["2", "3"], "evergreen": None},
            {"text": "", "claim_ids": ["1", "2"]},
            "junk",
        ]
    }
    assert parse_shared(data, claims, {"ev.md"}) == [
        {"text": "ok", "claim_ids": ["1", "2"], "evergreen": "ev.md"}
    ]
    assert parse_shared(data, claims, set())[0]["evergreen"] is None


def test_parse_stances_defaults_to_unrelated() -> None:
    from vault_compass.claims import ClaimItem

    claims = [ClaimItem(id=str(i), text="t", path=A, title="a") for i in range(4)]
    data = {"stances": {"0": "supporting", "1": "pushing_back", "2": "bogus"}}
    assert parse_stances(data, claims) == {
        "0": "supporting",
        "1": "pushing_back",
        "2": "unrelated",
        "3": "unrelated",
    }
    assert parse_stances({"stances": []}, claims[:1]) == {"0": "unrelated"}


# --- the run --------------------------------------------------------------------


def test_view_before_any_run_is_empty_and_stale(vault: Path) -> None:
    view = _client(None).get("/topics/big/claims").json()
    assert view == {
        "topic": "big",
        "read_notes": 0,
        "pending_notes": 3,
        "claim_count": 0,
        "questions": [],
        "question": None,
        "supporting": [],
        "pushing_back": [],
        "unrelated": 0,
        "shared": [],
        "stale": True,
    }


def test_run_extracts_proposes_and_finds_shared_claims(vault: Path) -> None:
    model = ScriptedModel(_full_script())
    response = _client(model).post("/topics/big/claims/run", json={})
    body = response.json()
    ids = _ids()
    assert response.status_code == 200
    assert (body["status"], body["message"], body["estimate_usd"]) == ("done", None, None)
    view = body["view"]
    assert view["read_notes"] == 3
    assert view["pending_notes"] == 0
    assert view["claim_count"] == 4
    assert view["questions"] == ["Do agents need plans?", "Do tools matter?", "Q3"]
    assert view["question"] is None
    assert view["stale"] is False
    shared = view["shared"]
    assert len(shared) == 1
    assert shared[0]["text"] == "Agents need tools."
    assert [(c["id"], c["path"], c["title"]) for c in shared[0]["claims"]] == [
        (ids["b"], B, "b"),
        (ids["k1"], KEY, "key"),
    ]
    assert shared[0]["evergreen"]["path"] == EVERGREEN
    assert len(model.prompts) == 3


def test_every_claim_links_to_its_note(vault: Path) -> None:
    _client(ScriptedModel(_full_script())).post("/topics/big/claims/run", json={})
    claims = _client(None).get("/topics/big/claims").json()["shared"][0]["claims"]
    with duckdb.connect(str(_db(vault)), read_only=True) as con:
        stored = {
            cid: path
            for cid, path in con.execute("SELECT claim_id, path FROM note_claims").fetchall()
        }
    assert {c["id"]: c["path"] for c in claims} == {c["id"]: stored[c["id"]] for c in claims}


def test_key_claims_notes_cost_no_model_call(vault: Path) -> None:
    model = ScriptedModel(_full_script())
    _client(model).post("/topics/big/claims/run", json={})
    assert KEY not in model.prompts[0]
    with duckdb.connect(str(_db(vault)), read_only=True) as con:
        rows = con.execute(
            "SELECT text, origin FROM note_claims WHERE path = ? ORDER BY text", [KEY]
        ).fetchall()
    assert rows == [("Plans fail.", "key_claims"), ("Tools help.", "key_claims")]


def test_second_run_does_not_call_the_model_again(vault: Path) -> None:
    model = ScriptedModel(_full_script())
    client = _client(model)
    first = client.post("/topics/big/claims/run", json={}).json()
    calls_after_first = len(model.prompts)
    second = client.post("/topics/big/claims/run", json={}).json()
    assert calls_after_first == 3
    assert len(model.prompts) == 3
    assert second == first


def test_cached_topic_needs_no_api_key(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _client(ScriptedModel(_full_script())).post("/topics/big/claims/run", json={})

    def no_key(_settings: CompassSettings) -> ScriptedModel:
        raise AssertionError("model must not be created on a cache hit")

    client = TestClient(create_app(CompassSettings(), today=lambda: TODAY, model_factory=no_key))
    assert client.post("/topics/big/claims/run", json={}).json()["status"] == "done"


def test_excluded_and_evergreen_notes_are_never_sent_to_the_model(vault: Path) -> None:
    model = ScriptedModel(_full_script())
    _client(model).post("/topics/big/claims/run", json={})
    sent = "\n".join(model.prompts)
    assert SECRET not in sent
    assert DIARY not in sent
    assert f"### NOTE {EVERGREEN}" not in model.prompts[0]
    assert "Agents need plans and tools." in model.prompts[2]


def test_excluded_notes_are_not_counted_as_readable(vault: Path) -> None:
    view = _client(None).get("/topics/big/claims").json()
    assert view["read_notes"] + view["pending_notes"] == 3


def test_changed_note_is_read_again(vault: Path) -> None:
    client = _client(ScriptedModel(_full_script()))
    client.post("/topics/big/claims/run", json={})
    _write(vault, A, "Agents plan differently now.")
    refresh_notes(vault, _db(vault), load_topics(vault / ".kai" / "topics.yaml"))
    view = client.get("/topics/big/claims").json()
    assert (view["pending_notes"], view["claim_count"], view["stale"]) == (1, 3, True)
    assert view["questions"] == []


def test_choosing_a_question_then_running_sorts_claims_into_two_sides(vault: Path) -> None:
    ids = _ids()
    model = ScriptedModel(
        [
            *_full_script(),
            {
                "stances": {
                    ids["a"]: "supporting",
                    ids["b"]: "pushing_back",
                    ids["k1"]: "supporting",
                    ids["k2"]: "unrelated",
                }
            },
        ]
    )
    client = _client(model)
    client.post("/topics/big/claims/run", json={})
    saved = client.put("/topics/big/claims/question", json={"question": "  My own question?  "})
    assert saved.status_code == 200
    assert saved.json()["question"] == "My own question?"
    assert saved.json()["stale"] is True
    view = client.post("/topics/big/claims/run", json={}).json()["view"]
    assert [c["id"] for c in view["supporting"]] == [ids["a"], ids["k1"]]
    assert [c["id"] for c in view["pushing_back"]] == [ids["b"]]
    assert view["unrelated"] == 1
    assert view["question"] == "My own question?"
    assert view["stale"] is False
    assert model.prompts[3].startswith("QUESTION\nMy own question?\n\nCLAIMS\n")
    assert len(model.prompts) == 4


def test_saving_a_question_keeps_the_proposals(vault: Path) -> None:
    client = _client(ScriptedModel(_full_script()))
    client.post("/topics/big/claims/run", json={})
    view = client.put("/topics/big/claims/question", json={"question": "Q?"}).json()
    assert view["questions"] == ["Do agents need plans?", "Do tools matter?", "Q3"]


def test_unlinked_supporting_notes_match_a_direct_query_on_links(vault: Path) -> None:
    ids = _ids()
    script = _full_script()
    script[2]["shared"][0]["claim_ids"] = [ids["a"], ids["b"], ids["k1"]]
    client = _client(ScriptedModel(script))
    shared = client.post("/topics/big/claims/run", json={}).json()["view"]["shared"][0]
    with duckdb.connect(str(_db(vault)), read_only=True) as con:
        expected = con.execute(
            """
            SELECT path FROM (VALUES (?), (?), (?)) AS s(path)
            WHERE path NOT IN (SELECT source_path FROM links WHERE target_path = ?)
            ORDER BY path
            """,
            [A, B, KEY, EVERGREEN],
        ).fetchall()
    assert [n["path"] for n in shared["unlinked_notes"]] == [p for (p,) in expected]
    assert [n["path"] for n in shared["unlinked_notes"]] == [B, KEY]
    assert [n["title"] for n in shared["unlinked_notes"]] == ["b", "key"]


def test_run_stops_for_approval_when_over_the_cost_limit(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.0001")
    model = ScriptedModel(_full_script())
    client = _client(model)
    body = client.post("/topics/big/claims/run", json={}).json()
    assert body["status"] == "needs_approval"
    assert body["message"].startswith("This action may cost up to $")
    assert body["estimate_usd"] > 0
    assert model.prompts == []
    approved = client.post("/topics/big/claims/run", json={"approved": True}).json()
    assert approved["status"] == "done"
    assert len(model.prompts) == 3


def test_run_stops_when_the_month_limit_is_reached(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_MONTHLY_LIMIT_USD", "1")
    log_usage(_db(vault), "ai_call", "chat", cost_usd=1.0)
    body = _client(ScriptedModel(_full_script())).post("/topics/big/claims/run", json={}).json()
    assert body["status"] == "needs_approval"
    assert "monthly limit" in body["message"]


def test_run_reports_a_missing_key(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vault_compass.ai_client import AiNotConfiguredError

    def factory(_settings: CompassSettings) -> ScriptedModel:
        raise AiNotConfiguredError("Set OPENROUTER_API_KEY in .env to use the chat.")

    client = TestClient(create_app(CompassSettings(), today=lambda: TODAY, model_factory=factory))
    body = client.post("/topics/big/claims/run", json={}).json()
    assert (body["status"], body["message"]) == (
        "not_configured",
        "Set OPENROUTER_API_KEY in .env to use the chat.",
    )


def test_run_keeps_free_claims_when_the_model_fails(vault: Path) -> None:
    class Failing:
        async def stream(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> AsyncIterator[ModelEvent]:
            raise RuntimeError("boom")
            yield  # pragma: no cover

    client = TestClient(
        create_app(CompassSettings(), today=lambda: TODAY, model_factory=lambda _s: Failing())
    )
    body = client.post("/topics/big/claims/run", json={}).json()
    assert (body["status"], body["message"]) == (
        "failed",
        "The model call failed. Check the server log, then try again.",
    )
    assert body["view"]["claim_count"] == 2
    assert body["view"]["pending_notes"] == 2


def test_run_fails_cleanly_on_an_unreadable_answer(vault: Path) -> None:
    body = _client(ScriptedModel(["not json at all"])).post("/topics/big/claims/run", json={})
    assert (body.json()["status"], body.json()["message"]) == (
        "failed",
        "The model's answer could not be read. Try again.",
    )


def test_run_reads_at_most_30_notes_and_analyses_after_the_rest(vault: Path) -> None:
    for i in range(32):
        _write(vault, f"notes/n{i:02}.md", f"Body {i}.")
    refresh_notes(vault, _db(vault), load_topics(vault / ".kai" / "topics.yaml"))
    answers: list[Any] = [{"claims": []} for _ in range(10)]
    model = ScriptedModel(answers)
    client = _client(model)
    first = client.post("/topics/big/claims/run", json={}).json()["view"]
    assert first["pending_notes"] == 5
    assert first["questions"] == []
    assert first["read_notes"] == 30


def test_endpoints_404_for_unknown_topics_and_before_a_scan(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(None)
    assert client.get("/topics/nope/claims").json() == {"detail": "Unknown topic: nope"}
    assert client.post("/topics/nope/claims/run", json={}).status_code == 404
    assert client.put("/topics/nope/claims/question", json={"question": "q"}).status_code == 404
    _db(vault).unlink()
    assert client.get("/topics/big/claims").json() == {
        "detail": "No topic data yet. Run `compass scan` first."
    }


@pytest.mark.parametrize("question", ["", "   ", "x" * 301])
def test_question_must_be_1_to_300_characters(vault: Path, question: str) -> None:
    response = _client(None).put("/topics/big/claims/question", json={"question": question})
    assert response.status_code == 422


# --- chat tool (C4, C5) -----------------------------------------------------------


def test_topic_claims_tool_returns_the_cached_view_without_a_model(vault: Path) -> None:
    client = _client(ScriptedModel(_full_script()))
    view = client.post("/topics/big/claims/run", json={}).json()["view"]
    tool = AiVault.from_settings(CompassSettings())
    assert run_tool(tool, "topic_claims", {"topic": "big"}) == view


def test_topic_claims_tool_explains_when_nothing_is_cached(vault: Path) -> None:
    tool = AiVault.from_settings(CompassSettings())
    assert run_tool(tool, "topic_claims", {"topic": "big"}) == {
        "error": "No claims have been read for this topic yet. "
        "Ask the user to open the topic page and choose Find claims."
    }
    assert run_tool(tool, "topic_claims", {"topic": "nope"}) == {"error": "Unknown topic: nope"}


def test_topic_claims_tool_needs_a_scan(vault: Path) -> None:
    tool = AiVault.from_settings(CompassSettings())
    _db(vault).unlink()
    assert run_tool(tool, "topic_claims", {"topic": "big"}) == {
        "error": "No topic data yet. Run `compass scan` first."
    }


def test_claims_from_a_newly_excluded_folder_are_hidden(vault: Path) -> None:
    client = _client(ScriptedModel(_full_script()))
    client.post("/topics/big/claims/run", json={})
    (vault / ".kai" / "topics.yaml").write_text(
        TOPICS_YAML.replace("[notes/reflections]", "[notes/reflections, notes/key.md]"),
        encoding="utf-8",
    )
    view = client.get("/topics/big/claims").json()
    assert view["claim_count"] == 2
    assert KEY not in json.dumps(view)
