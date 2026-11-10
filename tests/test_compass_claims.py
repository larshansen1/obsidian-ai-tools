"""Tests for claims across sources and where they agree (T3, T4, Q6, Q7, N4)."""

import json
import os
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import pytest
from fastapi.testclient import TestClient

from vault_compass.ai_client import AiNotConfiguredError, ModelEvent, TextDelta, Usage
from vault_compass.app import create_app
from vault_compass.claims import (
    AGREEMENT_KIND,
    AGREEMENT_PROGRESS_KIND,
    AGREEMENT_STATE_KIND,
    MAX_EVERGREENS,
    QUESTIONS_BASE_KIND,
    QUESTIONS_REDO_PERCENT,
    SUMMARY_KIND,
    ClaimItem,
    claim_id,
    question_views,
    questions_kept,
    save_analysis,
    signature,
    summary_view,
    unlinked_supporters,
)
from vault_compass.claims_run import (
    AGREEMENT_SYSTEM,
    CHUNK_CLAIMS,
    CONFIRM_SYSTEM,
    EXTRACT_SYSTEM,
    MAX_MATCHES_PER_CHUNK,
    NO_CLAIMS_SUMMARY,
    QUESTIONS_SYSTEM,
    STANCE_SYSTEM,
    SUMMARY_SYSTEM,
    UNREADABLE,
    _by_note,
    _chunks,
    _fit,
    _numbered,
    finalize_shared,
    key_claims,
    merge_matches,
    parse_extracted,
    parse_json_object,
    parse_matches,
    parse_questions,
    parse_stances,
    parse_summary,
)
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.topic_page import NoteRef
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


Kind = Literal["extract", "questions", "agree", "confirm", "stances", "summary"]
_KIND_OF = {
    EXTRACT_SYSTEM: "extract",
    QUESTIONS_SYSTEM: "questions",
    AGREEMENT_SYSTEM: "agree",
    CONFIRM_SYSTEM: "confirm",
    STANCE_SYSTEM: "stances",
    SUMMARY_SYSTEM: "summary",
}
CLAIMS_PER_NOTE = 4


def _by_claim_text(question: str, texts: list[str]) -> str:
    """ "claim 1" and "Tools help." support, "claim 2" and "Plans fail." push back, rest neither."""
    support = ("claim 1", "Tools help.")
    push = ("claim 2", "Plans fail.")
    return "".join("S" if t.endswith(support) else "P" if t.endswith(push) else "U" for t in texts)


class AutoModel:
    """Answers each step from its prompt, in the formats the prompts ask for.

    `fail_when(kind, n)` raises on the n-th (0-based) call of that kind; `raw` overrides the
    text of the n-th call of a kind. Every call is recorded as (kind, user prompt).
    """

    def __init__(
        self,
        *,
        stance: Callable[[str, list[str]], str] = _by_claim_text,
        fail_when: Callable[[str, int], bool] = lambda _kind, _n: False,
        raw: dict[tuple[str, int], str] | None = None,
    ) -> None:
        self.stance = stance
        self.fail_when = fail_when
        self.raw = raw or {}
        self.calls: list[tuple[str, str]] = []
        self.settings: list[CompassSettings] = []

    def kinds(self) -> list[str]:
        return [kind for kind, _user in self.calls]

    def users(self, kind: str) -> list[str]:
        return [user for k, user in self.calls if k == kind]

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ModelEvent]:
        assert tools == []
        assert [m["role"] for m in messages] == ["system", "user"]
        kind = _KIND_OF[messages[0]["content"]]
        user = messages[1]["content"]
        index = sum(1 for k, _u in self.calls if k == kind)
        self.calls.append((kind, user))
        if self.fail_when(kind, index):
            raise RuntimeError("boom")
        answer = self.raw.get((kind, index), self._answer(kind, user))
        yield TextDelta(answer if isinstance(answer, str) else json.dumps(answer))
        yield Usage(model="m/x", input_tokens=100, output_tokens=20, cost_usd=0.01)

    def _answer(self, kind: str, user: str) -> Any:
        if kind == "extract":
            blocks = user.split("### NOTE ")[1:]
            claims = []
            for block in blocks:
                path = block.split(" ", 1)[0]
                # A note that was edited ("Changed", "differently") yields different claims.
                mark = " v2" if ("Changed" in block or "differently" in block) else ""
                claims += [
                    {"note": path, "text": f"{path}{mark} claim {k}"}
                    for k in range(1, CLAIMS_PER_NOTE + 1)
                ]
            return {"claims": claims}
        if kind == "questions":
            # Anchor both questions in the first and the last claim, which are in different notes.
            numbers = [int(line.split(".", 1)[0]) for line in user.split("\n") if line[0].isdigit()]
            ends = [numbers[0], numbers[-1]]
            return {
                "questions": [
                    {"text": "Q one?", "kind": "tension", "why": "Notes differ.", "claims": ends},
                    {"text": "Q two?", "kind": "recurring", "why": "It recurs.", "claims": ends},
                ]
            }
        if kind in ("agree", "confirm"):
            # Every claim matches; the parsers keep 6 per chunk and 10 after confirming.
            lines = user.split("\n\nCLAIMS\n")[1].split("\n")
            label = "Shared point" if kind == "agree" else "Confirmed point"
            return {
                "point": "P",
                "text": f"{label} {len(lines)}",
                "c": list(range(1, len(lines) + 1)),
            }
        if kind == "summary":
            numbers = [int(line.split(" | ", 1)[0]) for line in user.split("\n") if " | " in line]
            return {
                "bottom_line": f"Split on {len(numbers)} claims.",
                "agree": ["Both sides agree."],
                "split": ["They part here."],
                "missing": ["A study."],
                "read": numbers[:1] + numbers[-1:],
            }
        question, lines = user.split("\n\nCLAIMS\n")
        texts = [line.split(". ", 1)[1] for line in lines.split("\n")]
        letters = self.stance(question.removeprefix("QUESTION\n"), texts)
        return {
            "supporting": [n for n, ch in enumerate(letters, start=1) if ch == "S"],
            "pushing_back": [n for n, ch in enumerate(letters, start=1) if ch == "P"],
        }


def _write(vault: Path, rel: str, body: str, links: str = "", tags: str = "[x]") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {path.stem}\ncreated: 2026-09-01\ntags: {tags}\n---\n{body}\n{links}\n",
        encoding="utf-8",
    )


def _rescan(vault: Path) -> None:
    refresh_notes(vault, _db(vault), load_topics(vault / ".kai" / "topics.yaml"))


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    _write(root, A, "Agents plan work.", links="See [[ev]].")
    _write(root, B, "Agents use tools.")
    _write(root, KEY, "Intro.\n\n## Key Claims\n- Tools help.\n- Plans fail.\n\n## Other\n- no\n")
    _write(root, EVERGREEN, "Agents need plans and tools.")
    _write(root, DIARY, f"Agents. {SECRET}")
    _write(root, "notes/other.md", "Gardening only.", tags="[y]")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(root))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    for key in ("COMPASS_DB_PATH", "COMPASS_TOPICS_PATH", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    # The default 16000-token questions budget alone nearly reaches the default $0.25 action
    # limit at the default prices; the tests that are about cost limits set their own values.
    monkeypatch.setenv("COMPASS_AI_QUESTIONS_MAX_OUTPUT_TOKENS", "4000")
    # The end-to-end tests follow every claim through agreement; the caps on matches per chunk
    # and per evergreen have their own tests (test_parse_matches_keeps_at_most_six_claims_...,
    # test_the_confirm_pass_keeps_at_most_ten_claims_closest_first).
    monkeypatch.setattr("vault_compass.claims_run.MAX_MATCHES_PER_CHUNK", 10_000)
    monkeypatch.setattr("vault_compass.claims_run.MAX_SHARED_CLAIMS", 10_000)
    _rescan(root)
    return root


def _db(vault: Path) -> Path:
    return vault / ".kai" / "compass.duckdb"


def _client(model: AutoModel | None) -> TestClient:
    def factory(settings: CompassSettings) -> AutoModel:
        assert model is not None, "the model must not be created"
        model.settings.append(settings)
        return model

    return TestClient(create_app(CompassSettings(), today=lambda: TODAY, model_factory=factory))


def _run(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/topics/big/claims/run", json=body)
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


def _texts(view: dict[str, Any]) -> list[str]:
    return [q["text"] for q in view["questions"]]


def _view(client: TestClient) -> dict[str, Any]:
    return client.get("/topics/big/claims").json()  # type: ignore[no-any-return]


def _many_notes(vault: Path, count: int) -> None:
    for i in range(count):
        _write(vault, f"notes/n{i:03}.md", f"Body {i}.")
    _rescan(vault)


@pytest.fixture
def big_vault(vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """3 source notes + 40 more: 42 read by the model (4 claims each) + 2 key claims = 170."""
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "100")
    _many_notes(vault, 40)
    return vault


TOTAL_BIG = 2 + CLAIMS_PER_NOTE * 42


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
    assert parse_extracted(data, {A, B}) == {A: ["one"], B: ["c0", "c1", "c2", "c3"]}


def _q(text: str, kind: str = "tension", claims: Any = (1, 2), why: Any = "W") -> dict[str, Any]:
    return {"text": text, "kind": kind, "why": why, "claims": list(claims)}


def _two_notes() -> list[ClaimItem]:
    return [_item(1, A), _item(2, B), _item(3, B)]


def test_parse_questions_resolves_claim_numbers_to_ids() -> None:
    data = {"questions": [_q(" Q1 ", why=" Notes differ. ", claims=[3, 1, 3, 9, 0])]}
    assert parse_questions(data, _two_notes()) == [
        {"text": "Q1", "kind": "tension", "why": "Notes differ.", "claim_ids": ["id3", "id1"]}
    ]


def test_parse_questions_needs_anchors_in_two_notes() -> None:
    claims = _two_notes()
    data = {"questions": [_q("one note", claims=[2, 3]), _q("none", claims=[]), _q("ok")]}
    assert [q["text"] for q in parse_questions(data, claims)] == ["ok"]


def test_parse_questions_accepts_the_three_kinds_and_spelling_variants() -> None:
    data = {
        "questions": [
            _q("a", "Tension"),
            _q("b", " recurring "),
            _q("c", "carry-over"),
            _q("d", "textbook"),
            _q("e", None),
        ]
    }
    assert [(q["text"], q["kind"]) for q in parse_questions(data, _two_notes())] == [
        ("a", "tension"),
        ("b", "recurring"),
        ("c", "carry_over"),
    ]


def test_parse_questions_keeps_one_carry_over_unique_texts_and_six_at_most() -> None:
    data = {
        "questions": [
            _q("c1", "carry_over"),
            _q("c2", "carry_over"),
            _q("Q1"),
            _q("Q1", "recurring"),
            *(_q(f"Q{i}") for i in range(2, 9)),
        ]
    }
    assert [q["text"] for q in parse_questions(data, _two_notes())] == [
        "c1",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
    ]


def test_parse_questions_cuts_long_why_lines_and_drops_odd_ones() -> None:
    data = {"questions": [_q("a", why="w" * 400), _q("b", why=7), _q("", why="x")]}
    parsed = parse_questions(data, _two_notes())
    assert [(q["text"], q["why"]) for q in parsed] == [("a", "w" * 300), ("b", "")]


def _item(i: int, path: str = A) -> ClaimItem:
    return ClaimItem(id=f"id{i}", text="t", path=path, title="x")


def _evergreens(n: int) -> list[NoteRef]:
    return [NoteRef(path=f"e{i}.md", title=f"E{i}", created=None) for i in range(1, n + 1)]


EV = NoteRef(path="e1.md", title="E1", created=None)


def test_parse_matches_maps_numbers_to_claim_ids_for_the_one_evergreen() -> None:
    chunk = [_item(i) for i in range(1, 5)]
    data = {"point": "P", "text": " Shared. ", "c": [3, 1, 3]}
    assert parse_matches(data, chunk, EV) == [("e1.md", "Shared.", ["id3", "id1"])]


def test_parse_matches_keeps_only_claim_numbers_in_range() -> None:
    chunk = [_item(1), _item(2)]
    data = {"text": "t", "c": [0, 1, 2, 3, True, "2", 2.0]}
    assert parse_matches(data, chunk, EV) == [("e1.md", "t", ["id1", "id2"])]


def test_parse_matches_keeps_at_most_six_claims_per_chunk() -> None:
    chunk = [_item(i) for i in range(1, 10)]
    data = {"text": "t", "c": list(range(1, 10))}
    assert parse_matches(data, chunk, EV) == [
        ("e1.md", "t", ["id1", "id2", "id3", "id4", "id5", "id6"])
    ]
    assert MAX_MATCHES_PER_CHUNK == 6


@pytest.mark.parametrize(
    "data",
    [
        {"text": "", "c": [1]},
        {"text": "t", "c": []},
        {"text": 3, "c": [1]},
        {"text": "t"},
        {},
        {"text": "t", "c": None},
    ],
)
def test_parse_matches_returns_nothing_for_an_incomplete_answer(data: dict[str, Any]) -> None:
    assert parse_matches(data, [_item(1)], EV) == []


def test_merge_matches_unions_ids_and_keeps_the_wording_of_the_biggest_match() -> None:
    found: dict[str, dict[str, Any]] = {}
    merge_matches(found, [("e1.md", "small", ["a"]), ("e1.md", "big", ["b", "a", "c"])])
    merge_matches(found, [("e1.md", "equal size", ["x", "y", "z"]), ("e2.md", "other", ["q"])])
    assert found == {
        "e1.md": {"text": "big", "best": 3, "claim_ids": ["a", "b", "c", "x", "y", "z"]},
        "e2.md": {"text": "other", "best": 1, "claim_ids": ["q"]},
    }


def test_finalize_shared_needs_two_distinct_notes_and_sorts_by_support() -> None:
    claims = [_item(1, A), _item(2, A), _item(3, B), _item(4, KEY)]
    found = {
        "e1.md": {"text": "same note", "best": 2, "claim_ids": ["id1", "id2"]},
        "e2.md": {"text": "two", "best": 2, "claim_ids": ["id1", "id3", "gone"]},
        "e3.md": {"text": "three", "best": 4, "claim_ids": ["id1", "id3", "id4", "id2"]},
    }
    assert finalize_shared(found, claims) == [
        {"text": "three", "claim_ids": ["id1", "id3", "id4", "id2"], "evergreen": "e3.md"},
        {"text": "two", "claim_ids": ["id1", "id3", "gone"], "evergreen": "e2.md"},
    ]


def test_parse_stances_reads_the_two_number_lists() -> None:
    chunk = [_item(i) for i in range(5)]
    data = {"supporting": [1, 4], "pushing_back": [2]}
    assert parse_stances(data, chunk) == {
        "id0": "supporting",
        "id1": "pushing_back",
        "id2": "unrelated",
        "id3": "supporting",
        "id4": "unrelated",
    }


def test_parse_stances_leaves_a_claim_listed_on_both_sides_unrelated() -> None:
    chunk = [_item(i) for i in range(2)]
    assert parse_stances({"supporting": [1, 2], "pushing_back": [2]}, chunk) == {
        "id0": "supporting",
        "id1": "unrelated",
    }


def test_parse_stances_ignores_numbers_outside_the_chunk() -> None:
    chunk = [_item(0), _item(1)]
    data = {"supporting": [0, 3, -1, 10**30, True, "1", 1.0], "pushing_back": [2]}
    assert parse_stances(data, chunk) == {"id0": "unrelated", "id1": "pushing_back"}


@pytest.mark.parametrize("data", [{}, {"supporting": 5, "pushing_back": "x"}, {"supporting": None}])
def test_parse_stances_treats_a_missing_or_wrong_answer_as_unrelated(data: dict[str, Any]) -> None:
    assert parse_stances(data, [_item(0)]) == {"id0": "unrelated"}


# --- chunking helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "sizes"),
    [
        (0, []),
        (1, [1]),
        (149, [149]),
        (150, [150]),
        (151, [150, 1]),
        (300, [150, 150]),
        (301, [150, 150, 1]),
    ],
)
def test_chunks_hold_at_most_150_claims(count: int, sizes: list[int]) -> None:
    assert CHUNK_CLAIMS == 150
    assert [len(c) for c in _chunks([_item(i) for i in range(count)])] == sizes


def test_chunks_keep_claim_order() -> None:
    claims = [_item(i) for i in range(151)]
    chunks = _chunks(claims)
    assert [c.id for c in chunks[0] + chunks[1]] == [c.id for c in claims]


def test_numbered_lines_start_at_one_with_or_without_titles() -> None:
    claims = [ClaimItem(id="i", text="T1", path=A, title="Ti"), _item(2)]
    assert _numbered(claims, titles=True) == "1 | Ti | T1\n2 | x | t"
    assert _numbered(claims, titles=False) == "1. T1\n2. t"


def test_by_note_groups_claims_under_their_note_and_numbers_them_throughout() -> None:
    claims = [
        ClaimItem(id="1", text="T1", path=A, title="Note A"),
        ClaimItem(id="2", text="T2", path=A, title="Note A"),
        ClaimItem(id="3", text="T3", path=B, title="Note B"),
    ]
    assert _by_note(claims) == "N1 Note A\n1. T1\n2. T2\nN2 Note B\n3. T3"


def test_fit_keeps_every_claim_while_the_prompt_fits(caplog: pytest.LogCaptureFixture) -> None:
    claims = [ClaimItem(id=str(i), text="t", path=A, title="x") for i in range(10)]
    # "N1 x" plus "<n>. t" lines: all 10 claims make exactly 55 characters.
    assert len(_by_note(claims)) == 55
    assert _fit(claims, 55) == claims
    assert caplog.records == []


def test_fit_thins_claims_evenly_over_the_cap_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    claims = [ClaimItem(id=str(i), text="t", path=A, title="x") for i in range(10)]
    # One character under the 55 of all claims: every second claim (29 characters) fits.
    assert len(_by_note(claims[::2])) == 29
    assert [c.id for c in _fit(claims, 54)] == ["0", "2", "4", "6", "8"]
    assert [r.getMessage() for r in caplog.records] == [
        "questions prompt too long: every 2th of 10 claims"
    ]


def test_the_question_prompt_cap_defaults_to_600000_characters(vault: Path) -> None:
    assert CompassSettings().compass_ai_questions_max_prompt_chars == 600_000


def test_the_question_prompt_cap_can_be_set(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The small topic's questions prompt is 3 note lines and 10 claims; 200 fits only half.
    monkeypatch.setenv("COMPASS_AI_QUESTIONS_MAX_PROMPT_CHARS", "200")
    model = AutoModel()
    _run(_client(model))
    (user,) = model.users("questions")
    assert len(_prompt_claims(user)) == 5
    assert len(user) <= 200


# --- a small topic, end to end ----------------------------------------------------


def test_view_before_any_run_is_empty_and_stale(vault: Path) -> None:
    assert _view(_client(None)) == {
        "topic": "big",
        "read_notes": 0,
        "pending_notes": 3,
        "claim_count": 0,
        "questions": [],
        "question": None,
        "supporting": [],
        "pushing_back": [],
        "unrelated": 0,
        "summary": None,
        "shared": [],
        "stale": True,
        "next_step": "read",
        "answer_outdated": False,
        "questions_current": False,
        "agreement_current": False,
    }


def test_first_run_reads_proposes_and_finds_shared_claims(vault: Path) -> None:
    model = AutoModel()
    body = _run(_client(model))
    assert (body["status"], body["message"], body["estimate_usd"]) == ("done", None, None)
    assert model.kinds() == ["extract", "questions", "agree", "confirm"]
    view = body["view"]
    assert (view["read_notes"], view["pending_notes"], view["claim_count"]) == (3, 0, 10)
    assert _texts(view) == ["Q one?", "Q two?"]
    assert (view["question"], view["stale"]) == (None, False)
    assert len(view["shared"]) == 1
    shared = view["shared"][0]
    assert shared["text"] == "Confirmed point 10"
    assert shared["evergreen"] == {"path": EVERGREEN, "title": "ev", "created": "2026-09-01"}
    assert len(shared["claims"]) == 10


def test_unlinked_supporting_notes_match_a_direct_query_on_links(vault: Path) -> None:
    shared = _run(_client(AutoModel()))["view"]["shared"][0]
    with duckdb.connect(str(_db(vault)), read_only=True) as con:
        expected = con.execute(
            """
            SELECT path FROM (VALUES (?), (?), (?)) AS s(path)
            WHERE path NOT IN (SELECT source_path FROM links WHERE target_path = ?)
            ORDER BY path
            """,
            [A, B, KEY, EVERGREEN],
        ).fetchall()
    assert [p for (p,) in expected] == [B, KEY]
    assert [n["path"] for n in shared["unlinked_notes"]] == [B, KEY]
    assert [n["title"] for n in shared["unlinked_notes"]] == ["b", "key"]


def test_every_claim_links_to_the_note_it_came_from(vault: Path) -> None:
    claims = _run(_client(AutoModel()))["view"]["shared"][0]["claims"]
    assert len(claims) == 10
    for claim in claims:
        if claim["path"] == KEY:
            assert claim["text"] in {"Tools help.", "Plans fail."}
        else:
            assert claim["text"].startswith(f"{claim['path']} claim ")
        assert claim["title"] == Path(claim["path"]).stem


def test_key_claims_notes_cost_no_model_call(vault: Path) -> None:
    model = AutoModel()
    _run(_client(model))
    assert KEY not in model.users("extract")[0]
    with duckdb.connect(str(_db(vault)), read_only=True) as con:
        rows = con.execute(
            "SELECT text, origin FROM note_claims WHERE path = ? ORDER BY text", [KEY]
        ).fetchall()
        origins = con.execute(
            "SELECT DISTINCT origin FROM note_claims WHERE path != ?", [KEY]
        ).fetchall()
    assert rows == [("Plans fail.", "key_claims"), ("Tools help.", "key_claims")]
    assert origins == [("model",)]


def test_second_run_does_not_call_the_model_again(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    first = _run(client)
    calls = list(model.calls)
    second = _run(client)
    assert model.calls == calls
    assert second == first


def test_a_new_client_reuses_the_cache_without_creating_a_model(vault: Path) -> None:
    first = _run(_client(AutoModel()))
    assert _run(_client(None)) == first


def _prompt_claims(user: str) -> list[str]:
    """The claim texts of a questions prompt, in their numbered order."""
    return [line.split(". ", 1)[1] for line in user.split("\n") if line[0].isdigit()]


def test_suggested_questions_carry_kind_why_and_anchor_claims(vault: Path) -> None:
    model = AutoModel()
    view = _run(_client(model))["view"]
    texts = _prompt_claims(model.users("questions")[0])
    # The fake anchors both questions in the first claim (note a) and the last (note key).
    anchors = [
        {"id": claim_id(A, texts[0]), "text": texts[0], "path": A, "title": "a"},
        {"id": claim_id(KEY, texts[-1]), "text": texts[-1], "path": KEY, "title": "key"},
    ]
    assert texts[0].startswith(f"{A} claim ")
    assert texts[-1] in ("Tools help.", "Plans fail.")
    assert view["questions"] == [
        {"text": "Q one?", "kind": "tension", "why": "Notes differ.", "anchors": anchors},
        {"text": "Q two?", "kind": "recurring", "why": "It recurs.", "anchors": anchors},
    ]


def test_the_questions_prompt_names_only_the_topic_source_notes(vault: Path) -> None:
    model = AutoModel()
    _run(_client(model))
    (user,) = model.users("questions")
    assert [line for line in user.split("\n") if line.startswith("N")] == ["N1 a", "N2 b", "N3 key"]
    assert len(_prompt_claims(user)) == 10


def test_a_run_with_no_questions_leaves_the_list_empty_and_finishes(vault: Path) -> None:
    model = AutoModel(raw={("questions", 0): '{"questions": []}'})
    body = _run(_client(model))
    assert (body["status"], body["view"]["questions"]) == ("done", [])
    assert (body["view"]["next_step"], body["view"]["stale"]) == ("done", False)


def test_questions_saved_in_the_old_format_are_suggested_again(vault: Path) -> None:
    client = _client(AutoModel())
    _run(client)
    with duckdb.connect(str(_db(vault))) as con:
        sig = con.execute("SELECT signature FROM topic_questions").fetchone()[0]
        con.execute(
            "UPDATE topic_questions SET proposals = ?, chosen = ?",
            [json.dumps(["Old question?"]), "Old question?"],
        )
    stale = _view(client)
    assert (stale["questions"], stale["question"], stale["next_step"]) == (
        [],
        "Old question?",
        "analyse",
    )
    model = AutoModel()
    fresh = _run(_client(model))["view"]
    assert model.kinds() == ["questions", "stances", "summary"]
    assert _texts(fresh) == ["Q one?", "Q two?"]
    assert fresh["question"] == "Old question?"
    with duckdb.connect(str(_db(vault))) as con:
        assert con.execute("SELECT signature FROM topic_questions").fetchone()[0] == sig


def test_question_views_drop_stale_anchors_and_questions_left_in_one_note() -> None:
    claims = _two_notes()
    proposals = [
        {"text": "keep", "kind": "tension", "why": "w", "claim_ids": ["id1", "gone", "id2"]},
        {"text": "one note", "kind": "recurring", "why": "w", "claim_ids": ["id1", "gone"]},
        {"text": "odd kind", "kind": "textbook", "why": "w", "claim_ids": ["id1", "id2"]},
        {"text": "no why", "kind": "carry_over", "claim_ids": ["id2", "id1"]},
    ]
    assert [q.model_dump() for q in question_views(proposals, claims)] == [
        {
            "text": "keep",
            "kind": "tension",
            "why": "w",
            "anchors": [claims[0].model_dump(), claims[1].model_dump()],
        },
        {
            "text": "no why",
            "kind": "carry_over",
            "why": "",
            "anchors": [claims[1].model_dump(), claims[0].model_dump()],
        },
    ]


def test_only_source_notes_of_the_topic_reach_the_model(vault: Path) -> None:
    model = AutoModel()
    _run(_client(model))
    sent = "\n".join(user for _kind, user in model.calls)
    assert SECRET not in sent
    assert DIARY not in sent
    assert "other.md" not in sent
    assert f"### NOTE {EVERGREEN}" not in sent
    assert sorted(re.findall(r"^### NOTE (\S+)", sent, re.MULTILINE)) == [A, B]
    assert "Agents need plans and tools." in model.users("agree")[0]


def test_changed_note_is_read_again_and_the_analysis_follows(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _run(client)
    _write(vault, A, "Agents plan differently now.", links="See [[ev]].")
    _rescan(vault)
    stale = _view(client)
    assert (stale["pending_notes"], stale["claim_count"], stale["stale"]) == (1, 6, True)
    assert stale["questions"] == []
    assert stale["shared"] == []  # its claims no longer match what was analysed
    model.calls.clear()
    fresh = _run(client)["view"]
    assert model.kinds() == ["extract", "questions", "agree", "confirm"]
    assert re.findall(r"^### NOTE (\S+)", model.users("extract")[0], re.MULTILINE) == [A]
    assert (fresh["claim_count"], fresh["stale"], fresh["pending_notes"]) == (10, False, 0)
    assert _texts(fresh) == ["Q one?", "Q two?"]


def test_unchanged_notes_are_not_read_again_after_another_changed(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _run(client)
    _write(vault, B, "Agents use tools differently.")
    _rescan(vault)
    model.calls.clear()
    _run(client)
    assert re.findall(r"^### NOTE (\S+)", model.users("extract")[0], re.MULTILINE) == [B]


def test_choosing_a_question_sorts_claims_into_two_sides(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _run(client)
    saved = client.put("/topics/big/claims/question", json={"question": "  My own question?  "})
    assert saved.status_code == 200
    assert (saved.json()["question"], saved.json()["stale"]) == ("My own question?", True)
    assert saved.json()["supporting"] == []
    model.calls.clear()
    view = _run(client)["view"]
    assert model.kinds() == ["stances", "summary"]
    assert model.users("stances")[0].startswith("QUESTION\nMy own question?\n\nCLAIMS\n1. ")
    assert sorted(c["text"] for c in view["supporting"]) == sorted(
        [f"{A} claim 1", f"{B} claim 1", "Tools help."]
    )
    assert sorted(c["text"] for c in view["pushing_back"]) == sorted(
        [f"{A} claim 2", f"{B} claim 2", "Plans fail."]
    )
    assert (view["unrelated"], view["question"], view["stale"]) == (4, "My own question?", False)


def test_saving_a_question_keeps_the_proposals(vault: Path) -> None:
    client = _client(AutoModel())
    _run(client)
    view = client.put("/topics/big/claims/question", json={"question": "Q?"}).json()
    assert _texts(view) == ["Q one?", "Q two?"]


def test_choosing_a_question_before_any_run_is_kept(vault: Path) -> None:
    client = _client(None)
    assert (
        client.put("/topics/big/claims/question", json={"question": "Q?"}).json()["question"]
        == "Q?"
    )
    assert _view(client)["question"] == "Q?"


def test_switching_questions_only_sorts_again(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _run(client)
    client.put("/topics/big/claims/question", json={"question": "First?"})
    first = _run(client)["view"]
    model.calls.clear()
    client.put("/topics/big/claims/question", json={"question": "Second?"})
    _run(client)
    assert model.kinds() == ["stances", "summary"]
    model.calls.clear()
    client.put("/topics/big/claims/question", json={"question": "First?"})
    assert _run(client)["view"] == first
    assert model.calls == []


def test_parse_summary_keeps_clean_points_and_one_claim_per_note() -> None:
    claims = [_item(1, A), _item(2, A), _item(3, B), _item(4, KEY)]
    data = {
        "bottom_line": "  They split.  ",
        "agree": [" One. ", "", 3, "Two."],
        "split": "not a list",
        "missing": ["m" * 700],
        "read": [2, 1, 3, 9, 3, 0],
    }
    assert parse_summary(data, claims) == {
        "bottom_line": "They split.",
        "agree": ["One.", "Two."],
        "split": [],
        "missing": ["m" * 600],
        "read_ids": ["id2", "id3"],
    }


def test_parse_summary_caps_points_at_six_and_reads_at_five() -> None:
    claims = [_item(i, f"notes/n{i}.md") for i in range(1, 9)]
    data = {"bottom_line": "B", "split": [f"p{i}" for i in range(9)], "read": list(range(1, 9))}
    parsed = parse_summary(data, claims)
    assert parsed is not None
    assert parsed["split"] == ["p0", "p1", "p2", "p3", "p4", "p5"]
    assert parsed["read_ids"] == ["id1", "id2", "id3", "id4", "id5"]


@pytest.mark.parametrize("bottom", [None, "", "   ", 3, ["x"]])
def test_parse_summary_needs_a_bottom_line(bottom: Any) -> None:
    assert parse_summary({"bottom_line": bottom, "agree": ["a"]}, [_item(1)]) is None


def test_parse_stances_ignores_the_named_subject() -> None:
    chunk = [_item(1), _item(2), _item(3)]
    data = {"subject": "one symptom", "supporting": [1], "pushing_back": [3]}
    assert parse_stances(data, chunk) == {
        "id1": "supporting",
        "id2": "unrelated",
        "id3": "pushing_back",
    }


def test_summary_view_resolves_reads_to_current_notes() -> None:
    claims = [_item(1, A), _item(2, B)]
    raw = {
        "bottom_line": "B",
        "agree": ["a"],
        "split": [],
        "missing": ["m"],
        "read_ids": ["gone", "id2", "id1"],
    }
    assert summary_view(raw, claims).model_dump() == {
        "bottom_line": "B",
        "agree": ["a"],
        "split": [],
        "missing": ["m"],
        "read": [
            {"path": B, "title": "x", "created": None},
            {"path": A, "title": "x", "created": None},
        ],
    }


def test_choosing_a_question_summarises_the_split(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _run(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    model.calls.clear()
    view = _run(client)["view"]
    assert model.kinds() == ["stances", "summary"]
    (user,) = model.users("summary")
    supporting = [f"{c['title']} | {c['text']}" for c in view["supporting"]]
    pushing = [f"{c['title']} | {c['text']}" for c in view["pushing_back"]]
    n = len(supporting)
    assert user == (
        "QUESTION\nQ?\n\nFOR\n"
        + "\n".join(f"{i} | {line}" for i, line in enumerate(supporting, start=1))
        + "\n\nAGAINST\n"
        + "\n".join(f"{n + i} | {line}" for i, line in enumerate(pushing, start=1))
    )
    first, last = view["supporting"][0], view["pushing_back"][-1]
    assert view["summary"] == {
        "bottom_line": f"Split on {n + len(pushing)} claims.",
        "agree": ["Both sides agree."],
        "split": ["They part here."],
        "missing": ["A study."],
        "read": [
            {"path": first["path"], "title": first["title"], "created": None},
            {"path": last["path"], "title": last["title"], "created": None},
        ],
    }
    assert (view["next_step"], view["stale"]) == ("done", False)
    assert _run(_client(None))["view"] == view  # cached: no model is even created


def _answered(client: TestClient) -> dict[str, Any]:
    """Read, choose "Q?", sort and summarise: the view with a current answer."""
    _run(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    return _run(client)["view"]  # type: ignore[no-any-return]


def _sides(view: dict[str, Any]) -> tuple[list[str], list[str]]:
    return (
        sorted(c["text"] for c in view["supporting"]),
        sorted(c["text"] for c in view["pushing_back"]),
    )


def test_a_current_answer_is_not_outdated(vault: Path) -> None:
    view = _answered(_client(AutoModel()))
    assert (view["answer_outdated"], view["next_step"]) == (False, "done")


def _open_work(view: dict[str, Any]) -> tuple[bool, bool, bool, str]:
    return (
        view["answer_outdated"],
        view["questions_current"],
        view["agreement_current"],
        view["next_step"],
    )


def test_a_finished_topic_has_current_questions_and_agreement(vault: Path) -> None:
    assert _open_work(_answered(_client(AutoModel()))) == (False, True, True, "done")


def test_a_new_evergreen_leaves_the_answer_current_and_only_agreement_open(vault: Path) -> None:
    client = _client(AutoModel())
    answered = _answered(client)
    _write(vault, "notes/evergreen/ev2.md", "Agents need memory.")
    _rescan(vault)

    view = _view(client)

    assert _open_work(view) == (False, True, False, "analyse")
    assert (view["summary"], _sides(view)) == (answered["summary"], _sides(answered))


def test_a_new_note_keeps_the_answer_and_marks_it_outdated(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    answered = _answered(client)
    _write(vault, "notes/new.md", "Agents again.")
    _rescan(vault)
    model.calls.clear()
    kept = _view(client)
    assert model.calls == []
    assert (kept["next_step"], kept["pending_notes"], kept["read_notes"]) == ("read", 1, 3)
    assert kept["answer_outdated"] is True
    assert kept["summary"] == answered["summary"]
    assert _sides(kept) == _sides(answered)
    assert kept["unrelated"] == 4


def test_a_changed_note_keeps_the_answer_until_a_run_updates_it(vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _answered(client)
    _write(vault, A, "Agents plan differently now.", links="See [[ev]].")
    _rescan(vault)
    kept = _view(client)
    assert (kept["next_step"], kept["pending_notes"], kept["answer_outdated"]) == ("read", 1, True)
    assert kept["summary"]["bottom_line"] == "Split on 6 claims."
    # The changed note's claims wait for the next read; the other notes' keep their sides.
    assert _sides(kept) == (
        sorted([f"{B} claim 1", "Tools help."]),
        sorted([f"{B} claim 2", "Plans fail."]),
    )
    assert kept["unrelated"] == 2
    model.calls.clear()
    fresh = _run(client)["view"]
    assert model.kinds() == ["extract", "questions", "agree", "confirm", "stances", "summary"]
    assert (fresh["next_step"], fresh["answer_outdated"], fresh["pending_notes"]) == (
        "done",
        False,
        0,
    )
    assert _sides(fresh) == (
        sorted([f"{A} v2 claim 1", f"{B} claim 1", "Tools help."]),
        sorted([f"{A} v2 claim 2", f"{B} claim 2", "Plans fail."]),
    )


def test_claims_read_after_the_answer_are_not_counted_as_unrelated(vault: Path) -> None:
    _answered(_client(AutoModel()))
    _write(vault, "notes/new.md", "Agents again.")
    _rescan(vault)
    # The new note is read, then the sort fails: 14 claims, 10 of them sorted earlier.
    body = _run(_client(AutoModel(fail_when=lambda kind, _n: kind == "stances")))
    view = body["view"]
    assert body["status"] == "failed"
    assert (view["claim_count"], view["pending_notes"], view["next_step"]) == (14, 0, "sort")
    assert (view["unrelated"], view["answer_outdated"]) == (4, True)
    assert view["summary"]["bottom_line"] == "Split on 6 claims."


def test_a_question_without_a_saved_answer_shows_none_while_notes_wait(vault: Path) -> None:
    client = _client(AutoModel())
    _run(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    _write(vault, "notes/new.md", "Agents again.")
    _rescan(vault)
    view = _view(client)
    assert (view["summary"], view["answer_outdated"], view["next_step"]) == (None, False, "read")
    assert _sides(view) == ([], [])


def test_a_question_no_claim_bears_on_is_summarised_without_a_model_call(vault: Path) -> None:
    model = AutoModel(stance=lambda _q, texts: "U" * len(texts))
    client = _client(model)
    _run(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    model.calls.clear()
    view = _run(client)["view"]
    assert model.kinds() == ["stances"]
    assert view["summary"] == {
        "bottom_line": NO_CLAIMS_SUMMARY,
        "agree": [],
        "split": [],
        "missing": [],
        "read": [],
    }
    assert NO_CLAIMS_SUMMARY == "No claims in these notes are about this question."


def test_an_unreadable_summary_fails_and_keeps_the_question_to_sort(vault: Path) -> None:
    model = AutoModel(raw={("summary", 0): '{"bottom_line": ""}'})
    client = _client(model)
    _run(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    body = _run(client)
    assert (body["status"], body["message"]) == ("failed", UNREADABLE)
    assert (body["view"]["summary"], body["view"]["next_step"]) == (None, "sort")
    retry = AutoModel()
    view = _run(_client(retry))["view"]
    assert retry.kinds() == ["summary"]  # the sort was kept; only the summary runs again
    assert view["summary"]["bottom_line"].startswith("Split on ")


def test_a_stance_answer_with_no_numbers_leaves_every_claim_unrelated(vault: Path) -> None:
    model = AutoModel(raw={("stances", 0): {"supporting": [], "pushing_back": []}})
    client = _client(model)
    _run(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    view = _run(client)["view"]
    assert (view["supporting"], view["pushing_back"], view["unrelated"]) == ([], [], 10)
    assert view["stale"] is False


def test_no_evergreens_means_no_agreement_call(vault: Path) -> None:
    (vault / EVERGREEN).unlink()
    _rescan(vault)
    model = AutoModel()
    view = _run(_client(model))["view"]
    assert model.kinds() == ["extract", "questions"]
    assert (view["shared"], view["stale"]) == ([], False)


def test_evergreens_in_an_excluded_folder_are_not_sent(vault: Path) -> None:
    (vault / ".kai" / "topics.yaml").write_text(
        TOPICS_YAML.replace("[notes/reflections]", "[notes/reflections, notes/evergreen]"),
        encoding="utf-8",
    )
    model = AutoModel()
    view = _run(_client(model))["view"]
    assert model.kinds() == ["extract", "questions"]
    assert "Agents need plans and tools." not in "".join(u for _k, u in model.calls)
    assert view["shared"] == []


def test_at_most_forty_evergreens_are_offered(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 40 calls at the default prices pass the $0.25 action limit; this test is about the cap.
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "100")
    assert MAX_EVERGREENS == 40
    for i in range(45):
        _write(vault, f"notes/evergreen/z{i:02}.md", f"Idea {i}.")
    _rescan(vault)
    model = AutoModel()
    body = _run(_client(model))
    assert (body["status"], body["message"]) == ("done", None)
    # One evergreen per call: the small topic is one chunk, so one call per evergreen offered.
    offered = [u.split("EVERGREEN\n")[1].split("\n\nCLAIMS\n")[0] for u in model.users("agree")]
    assert len(offered) == 40
    assert offered[0] == "ev | Agents need plans and tools."
    assert offered[-1] == "z38 | Idea 38."


def test_an_evergreen_no_claim_matches_is_not_confirmed_or_shared(vault: Path) -> None:
    model = AutoModel(raw={("agree", 0): {"point": "P", "text": "t", "c": []}})
    view = _run(_client(model))["view"]
    assert (view["shared"], view["stale"]) == ([], False)
    assert model.kinds() == ["extract", "questions", "agree"]


def test_a_match_backed_by_one_note_is_not_shared(vault: Path) -> None:
    model = AutoModel(raw={("agree", 0): {"point": "P", "text": "t", "c": [1]}})
    assert _run(_client(model))["view"]["shared"] == []
    assert model.kinds() == ["extract", "questions", "agree", "confirm"]


# --- limits and failures ------------------------------------------------------------


def test_run_stops_for_approval_when_over_the_cost_limit(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.0001")
    model = AutoModel()
    client = _client(model)
    body = _run(client)
    assert body["status"] == "needs_approval"
    assert body["message"].startswith("This action may cost up to $")
    assert body["estimate_usd"] > 0
    assert model.calls == []
    assert _run(client, approved=True)["status"] == "done"
    assert model.kinds() == ["extract", "questions", "agree", "confirm"]


def test_run_stops_when_the_month_limit_is_reached(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_MONTHLY_LIMIT_USD", "1")
    log_usage(_db(vault), "ai_call", "chat", cost_usd=1.0)
    body = _run(_client(AutoModel()))
    assert body["status"] == "needs_approval"
    assert "monthly limit" in body["message"]


def test_a_run_stopped_by_the_limit_resumes_without_repeating_work(
    big_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.08")
    model = AutoModel()
    client = _client(model)
    first = _run(client)
    assert first["status"] == "needs_approval"
    assert model.kinds() == ["extract", "extract"]
    done_after_stop = first["view"]["read_notes"]
    assert 0 < done_after_stop < 43
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "100")
    # Each run reads at most 30 notes, then analyses once all are read.
    while _run(client)["view"]["pending_notes"]:
        pass
    _run(client)
    read = re.findall(r"^### NOTE (\S+)", "\n".join(model.users("extract")), re.MULTILINE)
    assert len(read) == len(set(read)) == 42


def test_claims_calls_turn_thinking_off_but_the_questions_step_thinks(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COMPASS_AI_QUESTIONS_MAX_OUTPUT_TOKENS")
    monkeypatch.setenv("COMPASS_AI_REASONING", "false")
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "100")
    model = AutoModel()
    _run(_client(model))
    # One model for reading and agreement, one for the questions step.
    assert model.kinds() == ["extract", "questions", "agree", "confirm"]
    plain, thinking = model.settings
    assert (plain.compass_ai_reasoning, plain.compass_ai_max_output_tokens) == (False, 4000)
    assert (thinking.compass_ai_reasoning, thinking.compass_ai_max_output_tokens) == (True, 16000)


def test_the_output_budget_for_questions_can_be_set(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_QUESTIONS_MAX_OUTPUT_TOKENS", "9000")
    model = AutoModel()
    _run(_client(model))
    assert [s.compass_ai_max_output_tokens for s in model.settings] == [4000, 9000]


def test_with_default_prices_the_questions_step_asks_before_spending(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 16000 output tokens at $15 per million is $0.24; with the input it passes the $0.25 limit.
    monkeypatch.delenv("COMPASS_AI_QUESTIONS_MAX_OUTPUT_TOKENS")
    model = AutoModel()
    body = _run(_client(model))
    assert (body["status"], body["view"]["next_step"]) == ("needs_approval", "analyse")
    assert body["message"] == "This action may cost up to $0.25, over the $0.25 per-action limit."
    assert body["estimate_usd"] == 0.2414  # $0.24 of output plus the input
    assert model.kinds() == ["extract"]


def test_the_output_budget_for_claims_can_be_set(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_AI_CLAIMS_MAX_OUTPUT_TOKENS", "2500")
    model = AutoModel()
    _run(_client(model))
    assert model.settings[0].compass_ai_max_output_tokens == 2500


def test_the_cost_estimate_uses_the_claims_budget(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 4000 output tokens at $15 per million is $0.06 before any input is counted.
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.05")
    body = _run(_client(AutoModel()))
    assert body["status"] == "needs_approval"
    assert 0.06 <= body["estimate_usd"] < 0.07


def test_an_empty_answer_names_the_likely_cause(vault: Path) -> None:
    model = AutoModel(raw={("extract", 0): "", ("extract", 1): "   "})
    body = _run(_client(model))
    assert body["status"] == "failed"
    assert body["message"] == (
        "The model sent an empty answer. A thinking model can use all its output on thinking: "
        "raise COMPASS_AI_CLAIMS_MAX_OUTPUT_TOKENS or pick another model with LLM_MODEL."
    )


def test_next_step_walks_through_read_analyse_sort_done(vault: Path) -> None:
    client = _client(AutoModel(fail_when=lambda kind, _n: kind == "questions"))
    assert _view(client)["next_step"] == "read"
    body = _run(client)  # reads the notes, then the questions call fails
    assert (body["status"], body["view"]["next_step"]) == ("failed", "analyse")
    client = _client(AutoModel())
    assert _run(client)["view"]["next_step"] == "done"
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    assert _view(client)["next_step"] == "sort"
    assert _run(client)["view"]["next_step"] == "done"


def test_next_step_is_read_while_notes_are_unread(vault: Path) -> None:
    _many_notes(vault, 32)
    client = _client(AutoModel())
    assert _run(client)["view"]["next_step"] == "read"
    assert _run(client)["view"]["next_step"] == "done"


def test_run_reports_a_missing_key(vault: Path) -> None:
    def factory(_settings: CompassSettings) -> AutoModel:
        raise AiNotConfiguredError("Set OPENROUTER_API_KEY in .env to use the chat.")

    client = TestClient(create_app(CompassSettings(), today=lambda: TODAY, model_factory=factory))
    body = _run(client)
    assert (body["status"], body["message"]) == (
        "not_configured",
        "Set OPENROUTER_API_KEY in .env to use the chat.",
    )
    assert body["view"]["claim_count"] == 2  # key claims need no model


def test_run_keeps_free_claims_when_the_model_fails(vault: Path) -> None:
    model = AutoModel(fail_when=lambda _kind, _n: True)
    body = _run(_client(model))
    assert (body["status"], body["message"]) == (
        "failed",
        "The model call failed. Check the server log, then try again.",
    )
    assert (body["view"]["claim_count"], body["view"]["pending_notes"]) == (2, 2)


def test_run_fails_cleanly_when_even_single_notes_are_unreadable(vault: Path) -> None:
    model = AutoModel(raw={("extract", 0): "not json at all", ("extract", 1): "still not json"})
    body = _run(_client(model))
    assert (body["status"], body["message"]) == (
        "failed",
        "The model's answer could not be read. Try again.",
    )
    assert body["view"]["claim_count"] == 2


def test_unreadable_batch_is_retried_one_note_at_a_time(vault: Path) -> None:
    model = AutoModel(raw={("extract", 0): '{"claims": [{"note": "notes/a.md", "text": "cut off'})
    view = _run(_client(model))["view"]
    assert model.kinds() == ["extract", "extract", "extract", "questions", "agree", "confirm"]
    assert [u.count("### NOTE") for u in model.users("extract")] == [2, 1, 1]
    assert (view["claim_count"], view["pending_notes"]) == (10, 0)


def test_a_failed_batch_does_not_lose_the_batches_already_saved(vault: Path) -> None:
    _many_notes(vault, 6)  # 8 notes for the model: two batches of 4
    model = AutoModel(fail_when=lambda kind, n: kind == "extract" and n == 1)
    client = _client(model)
    body = _run(client)
    assert body["status"] == "failed"
    view = _view(client)
    assert (view["claim_count"], view["read_notes"], view["pending_notes"]) == (
        2 + 4 * CLAIMS_PER_NOTE,
        1 + 4,
        4,
    )


def test_notes_are_sent_in_batches_of_at_most_four(vault: Path) -> None:
    _many_notes(vault, 6)
    model = AutoModel()
    _run(_client(model))
    assert [u.count("### NOTE") for u in model.users("extract")] == [4, 4]


@pytest.mark.parametrize("bad", [5, 1.5, True, "text", {"a": 1}, [[1]], [{"a": 1}], [None]])
def test_parsers_ignore_values_of_the_wrong_type(bad: Any) -> None:
    chunk = [_item(1)]
    assert parse_extracted({"claims": bad}, {A}) == {}
    assert parse_questions({"questions": bad}, chunk) == []
    odd_kind = {"text": "q", "kind": bad, "why": "w", "claims": [1, 2]}
    assert parse_questions({"questions": [odd_kind]}, _two_notes()) == []
    stored = {"text": "q", "kind": bad, "why": "w", "claim_ids": ["id1", "id2"]}
    assert question_views([stored], _two_notes()) == []
    assert (
        parse_questions({"questions": [{"text": "q", "kind": "tension", "claims": bad}]}, chunk)
        == []
    )
    if not isinstance(bad, str):  # any string is a valid shared-point line
        assert parse_matches({"text": bad, "c": [1]}, chunk, EV) == []
    assert parse_matches({"text": "t", "c": bad}, chunk, EV) == []
    assert parse_extracted({"claims": [{"note": bad, "text": "t"}]}, {A}) == {}


def test_an_unexpected_crash_becomes_a_failed_message_not_a_500(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise KeyError("surprise")

    monkeypatch.setattr("vault_compass.claims_run.parse_extracted", boom)
    body = _run(_client(AutoModel()))
    assert body["status"] == "failed"
    assert body["message"] == "Unexpected error (KeyError: 'surprise'). See the server log."


def test_a_blank_note_is_marked_read_without_a_model_call(vault: Path) -> None:
    _write(vault, "notes/blank.md", "   ")
    _rescan(vault)
    model = AutoModel()
    view = _run(_client(model))["view"]
    assert "notes/blank.md" not in "".join(model.users("extract"))
    assert (view["pending_notes"], view["claim_count"]) == (0, 10)


def test_a_note_is_cut_to_3000_characters(vault: Path) -> None:
    _write(vault, "notes/long.md", "w" * 5000)
    _rescan(vault)
    model = AutoModel()
    _run(_client(model))
    longest = max(len(line) for u in model.users("extract") for line in u.split("\n"))
    assert longest == 3000


def test_run_reads_at_most_30_notes_then_continues(vault: Path) -> None:
    _many_notes(vault, 32)
    model = AutoModel()
    client = _client(model)
    first = _run(client)["view"]
    assert (first["read_notes"], first["pending_notes"]) == (30, 5)
    assert (first["questions"], first["shared"]) == ([], [])
    assert set(model.kinds()) == {"extract"}
    second = _run(client)["view"]
    assert (second["read_notes"], second["pending_notes"], second["stale"]) == (35, 0, False)
    assert _texts(second) == ["Q one?", "Q two?"]
    assert model.kinds()[-3:] == ["questions", "agree", "confirm"]


# --- a topic big enough to need chunks -----------------------------------------------


def _finish_reading(client: TestClient) -> dict[str, Any]:
    view = _run(client)["view"]
    while view["pending_notes"]:
        view = _run(client)["view"]
    return view


def test_a_big_topic_is_analysed_in_chunks_of_150_claims(big_vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    view = _finish_reading(client)
    assert (view["read_notes"], view["claim_count"]) == (43, TOTAL_BIG)
    # 42 notes for the model in batches of 4: 29 in the first run (8 calls), 13 in the second (4).
    assert model.kinds().count("extract") == 12
    assert model.kinds().count("questions") == 1
    sizes = [len(u.split("\n\nCLAIMS\n")[1].split("\n")) for u in model.users("agree")]
    assert sizes == [150, 20]
    # The confirm pass sees all of the evergreen's candidates at once.
    (confirm,) = model.users("confirm")
    assert len(confirm.split("\n\nCLAIMS\n")[1].split("\n")) == TOTAL_BIG
    shared = view["shared"]
    assert len(shared) == 1
    assert shared[0]["text"] == f"Confirmed point {TOTAL_BIG}"
    assert len(shared[0]["claims"]) == TOTAL_BIG
    assert len({c["id"] for c in shared[0]["claims"]}) == TOTAL_BIG
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    model.calls.clear()
    sorted_view = _run(client)["view"]
    lines = [len(u.split("\n\nCLAIMS\n")[1].split("\n")) for u in model.users("stances")]
    assert lines == [75, 75, 20]
    assert len(sorted_view["supporting"]) == 43  # 42 "claim 1" + "Tools help."
    assert len(sorted_view["pushing_back"]) == 43  # 42 "claim 2" + "Plans fail."
    assert sorted_view["unrelated"] == 84  # the "claim 3" and "claim 4" of 42 notes


def test_the_questions_prompt_holds_every_claim_grouped_by_note(big_vault: Path) -> None:
    model = AutoModel()
    _finish_reading(_client(model))
    lines = model.users("questions")[0].split("\n")
    headers = [line for line in lines if line.startswith("N")]
    numbered = [line for line in lines if line[0].isdigit()]
    assert len(numbered) == TOTAL_BIG
    assert [int(line.split(".", 1)[0]) for line in numbered] == list(range(1, TOTAL_BIG + 1))
    assert len(headers) == 43  # key plus the 42 notes with four claims; never the evergreen
    assert headers[0] == "N1 a"


def test_a_stopped_sort_resumes_at_the_chunk_it_stopped_on(big_vault: Path) -> None:
    client = _client(AutoModel())
    _finish_reading(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    failing = AutoModel(fail_when=lambda kind, n: kind == "stances" and n == 1)
    client = _client(failing)
    body = _run(client)
    assert body["status"] == "failed"
    view = body["view"]
    assert (view["supporting"], view["pushing_back"], view["unrelated"]) == ([], [], 0)
    assert view["stale"] is True  # half a sort is never shown as the answer
    first_chunk_lines = failing.users("stances")[0].split("\n\nCLAIMS\n")[1].split("\n")

    assert len(first_chunk_lines) == 75

    healthy = AutoModel()
    final = _run(_client(healthy))["view"]
    assert healthy.kinds() == ["stances", "stances", "summary"]
    resumed = [u.split("\n\nCLAIMS\n")[1].split("\n") for u in healthy.users("stances")]
    assert [len(r) for r in resumed] == [75, 20]
    assert not set(first_chunk_lines) & {line for r in resumed for line in r}
    assert final["stale"] is False
    assert len(final["supporting"]) + len(final["pushing_back"]) + final["unrelated"] == TOTAL_BIG
    assert _run(_client(None))["view"] == final


def test_sorts_saved_in_an_older_format_are_ignored(big_vault: Path) -> None:
    client = _client(AutoModel())
    _finish_reading(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    view = _view(client)
    sig = signature(c["id"] for c in view["shared"][0]["claims"])
    garbage = {c["id"]: "supporting" for c in view["shared"][0]["claims"]}
    with duckdb.connect(str(_db(big_vault))) as con:
        for kind in ("stances", "stances_progress", "stances_v2", "stances_progress_v2"):
            save_analysis(con, "big", kind, "Q?", sig, garbage)
    again = _view(client)
    assert (again["next_step"], again["supporting"]) == ("sort", [])
    model = AutoModel()
    final = _run(_client(model))["view"]
    assert model.kinds() == ["stances", "stances", "stances", "summary"]
    assert len(final["supporting"]) == 43


def test_a_stopped_agreement_resumes_at_the_chunk_it_stopped_on(big_vault: Path) -> None:
    failing = AutoModel(fail_when=lambda kind, n: kind == "agree" and n == 1)
    client = _client(failing)
    _finish_reading(client)  # extraction and questions complete, agreement stops
    view = _view(client)
    assert (view["shared"], view["stale"], view["claim_count"]) == ([], True, TOTAL_BIG)
    assert failing.kinds().count("agree") == 2
    healthy = AutoModel()
    final = _run(_client(healthy))["view"]
    assert healthy.kinds() == ["agree", "confirm"]
    assert len(healthy.users("agree")[0].split("\n\nCLAIMS\n")[1].split("\n")) == 20
    assert final["stale"] is False
    assert len(final["shared"][0]["claims"]) == TOTAL_BIG
    assert final["shared"][0]["text"] == f"Confirmed point {TOTAL_BIG}"


def test_the_confirm_pass_keeps_at_most_ten_claims_closest_first(
    big_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vault_compass.claims_run.MAX_MATCHES_PER_CHUNK", 6)
    monkeypatch.setattr("vault_compass.claims_run.MAX_SHARED_CLAIMS", 10)
    reverse = {"point": "P", "text": "Kept.", "c": list(range(12, 0, -1))}
    model = AutoModel(raw={("confirm", 0): reverse})
    view = _finish_reading(_client(model))
    (user,) = model.users("confirm")
    head, claims = user.split("\n\nCLAIMS\n")
    assert head == "EVERGREEN\nev | Agents need plans and tools."
    lines = claims.split("\n")
    # Six candidates from each of the two chunks, numbered from 1 in the order they were found.
    assert [line.split(" | ", 1)[0] for line in lines] == [str(n) for n in range(1, 13)]
    texts = [line.split(" | ", 2)[2] for line in lines]
    (shared,) = view["shared"]
    assert (shared["text"], [c["text"] for c in shared["claims"]]) == ("Kept.", texts[::-1][:10])


def test_the_confirm_pass_can_keep_nothing(vault: Path) -> None:
    model = AutoModel(raw={("confirm", 0): {"point": "P", "text": "None.", "c": []}})
    view = _run(_client(model))["view"]
    assert (view["shared"], view["stale"]) == ([], False)


def test_a_stopped_confirm_pass_resumes_without_matching_again(vault: Path) -> None:
    failing = AutoModel(fail_when=lambda kind, _n: kind == "confirm")
    client = _client(failing)
    assert _run(client)["status"] == "failed"
    assert failing.kinds() == ["extract", "questions", "agree", "confirm"]
    healthy = AutoModel()
    view = _run(_client(healthy))["view"]
    assert healthy.kinds() == ["confirm"]
    assert view["shared"][0]["text"] == "Confirmed point 10"


def test_matches_saved_before_the_strict_prompt_are_redone(vault: Path) -> None:
    client = _client(AutoModel())
    view = _run(client)["view"]
    ids = [c["id"] for c in view["shared"][0]["claims"]]
    sig = signature(c for c in ids)
    with duckdb.connect(str(_db(vault))) as con:
        con.execute("DELETE FROM claim_analysis WHERE kind LIKE 'agreement%'")
        for kind in ("agreement", "agreement_progress"):
            loose = [{"text": "Loose.", "claim_ids": ids, "evergreen": EVERGREEN}]
            save_analysis(con, "big", kind, "", sig, loose)
    assert (_view(client)["shared"], _view(client)["next_step"]) == ([], "analyse")
    model = AutoModel()
    redone = _run(_client(model))["view"]
    assert model.kinds() == ["agree", "confirm"]
    assert redone["shared"][0]["text"] == "Confirmed point 10"


def _claims_sent(user: str) -> list[str]:
    """The claim texts of an agree, confirm or stances prompt, in their numbered order."""
    lines = user.split("\n\nCLAIMS\n")[1].split("\n")
    return [line.split(" | ", 2)[2] if " | " in line else line.split(". ", 1)[1] for line in lines]


def _sorted_big(client: TestClient) -> dict[str, Any]:
    """The big topic read, analysed and sorted by "Q?"."""
    _finish_reading(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    return _run(client)["view"]


N000_V2 = [f"notes/n000.md v2 claim {k}" for k in range(1, 5)]


def test_one_changed_note_sends_only_its_new_claims(big_vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _sorted_big(client)
    _write(big_vault, "notes/n000.md", "Changed body.")
    _rescan(big_vault)
    model.calls.clear()
    view = _run(client)["view"]
    # 8 of 170 claims changed: under 10%, so no questions call.
    assert model.kinds() == ["extract", "agree", "confirm", "stances", "summary"]
    assert re.findall(r"^### NOTE (\S+)", model.users("extract")[0], re.MULTILINE) == [
        "notes/n000.md"
    ]
    assert sorted(_claims_sent(model.users("agree")[0])) == N000_V2
    assert sorted(_claims_sent(model.users("stances")[0])) == N000_V2
    # The confirm call sees every candidate of the evergreen, old and new.
    assert len(_claims_sent(model.users("confirm")[0])) == TOTAL_BIG
    assert (view["claim_count"], view["stale"], view["next_step"]) == (TOTAL_BIG, False, "done")
    assert _texts(view) == ["Q one?", "Q two?"]
    supporting = {c["text"] for c in view["supporting"]}
    assert ("notes/n000.md v2 claim 1" in supporting, "notes/n000.md claim 1" in supporting) == (
        True,
        False,
    )
    assert len(view["supporting"]) == 43


def test_an_incremental_run_matches_a_full_run_for_every_claim(big_vault: Path) -> None:
    client = _client(AutoModel())
    _sorted_big(client)
    _write(big_vault, "notes/n000.md", "Changed body.")
    _write(big_vault, "notes/n100.md", "Body 100.")
    (big_vault / "notes/n039.md").unlink()
    _rescan(big_vault)
    incremental = _run(client)["view"]
    with duckdb.connect(str(_db(big_vault))) as con:
        con.execute("DELETE FROM claim_analysis")
    model = AutoModel()
    full = _run(_client(model))["view"]
    assert model.kinds() == ["questions", "agree", "agree", "confirm"] + ["stances"] * 3 + [
        "summary"
    ]
    for key in ("supporting", "pushing_back", "unrelated", "claim_count"):
        assert incremental[key] == full[key]

    # Kept claims come in the model's order (closest first), so compare them as sets.
    def kept(view: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
        return [
            (s["text"], s["evergreen"]["path"], sorted(c["id"] for c in s["claims"]))
            for s in view["shared"]
        ]

    assert kept(incremental) == kept(full)
    assert len(kept(full)[0][2]) == TOTAL_BIG


def test_removing_a_note_drops_its_claims_without_a_full_run(big_vault: Path) -> None:
    # Anchor both questions in notes a, b and the last note, n039.
    questions = {
        "questions": [
            {"text": "Q one?", "kind": "tension", "why": "W.", "claims": [1, 5, TOTAL_BIG]},
        ]
    }
    model = AutoModel(raw={("questions", 0): questions})
    client = _client(model)
    before = _sorted_big(client)
    assert [a["path"] for a in before["questions"][0]["anchors"]] == [A, B, "notes/n039.md"]
    (big_vault / "notes/n039.md").unlink()
    _rescan(big_vault)
    model.calls.clear()
    view = _run(client)["view"]
    # The evergreen lost kept claims, so it is confirmed again; the sides changed, so the
    # summary is redone. Nothing is matched, sorted or suggested again.
    assert model.kinds() == ["confirm", "summary"]
    assert len(_claims_sent(model.users("confirm")[0])) == TOTAL_BIG - 4
    assert view["claim_count"] == TOTAL_BIG - 4
    assert [a["path"] for a in view["questions"][0]["anchors"]] == [A, B]
    gone = "notes/n039.md"
    assert [c for c in view["supporting"] + view["pushing_back"] if c["path"] == gone] == []
    assert [c for c in view["shared"][0]["claims"] if c["path"] == gone] == []
    assert (len(view["supporting"]), len(view["pushing_back"]), view["unrelated"]) == (42, 42, 82)


def test_questions_are_kept_under_ten_percent_of_changed_claims() -> None:
    base = [f"c{i}" for i in range(100)]
    nine = base[4:] + ["new1", "new2", "new3", "new4", "new5"]  # 4 removed + 5 added
    ten = base[5:] + ["new1", "new2", "new3", "new4", "new5"]  # 5 removed + 5 added
    assert questions_kept(base, base) is True
    assert questions_kept(base, nine) is True
    assert questions_kept(base, ten) is False
    assert questions_kept([], []) is False
    assert QUESTIONS_REDO_PERCENT == 10


def test_questions_are_suggested_again_at_exactly_ten_percent(big_vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _finish_reading(client)
    # Two changed notes: 8 removed + 8 added = 16 of 170 claims, under 17 (10%).
    _write(big_vault, "notes/n000.md", "Changed body.")
    _write(big_vault, "notes/n001.md", "Changed body.")
    _rescan(big_vault)
    model.calls.clear()
    kept = _run(client)["view"]
    assert model.kinds().count("questions") == 0
    assert (_texts(kept), kept["next_step"]) == (["Q one?", "Q two?"], "done")
    # One more claim, free from a Key Claims note: 17 changed since the questions were made.
    _write(big_vault, "notes/one.md", "## Key Claims\n- One more.")
    _rescan(big_vault)
    model.calls.clear()
    redone = _run(client)["view"]
    assert model.kinds() == ["questions", "agree", "confirm"]
    assert redone["claim_count"] == TOTAL_BIG + 1
    assert len(_prompt_claims(model.users("questions")[0])) == TOTAL_BIG + 1


def test_new_proposals_measure_change_from_their_own_claims(big_vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _finish_reading(client)
    for i in range(5):  # 40 of 170 changed: suggested again from the 170 current claims
        _write(big_vault, f"notes/n{i:03}.md", "Changed body.")
    _rescan(big_vault)
    _run(client)
    model.calls.clear()
    _write(big_vault, "notes/n010.md", "Changed body.")  # 8 more: under 10% of the new set
    _rescan(big_vault)
    _run(client)
    assert model.kinds() == ["extract", "agree", "confirm"]


def test_a_stopped_incremental_run_resumes_without_redoing_work(big_vault: Path) -> None:
    client = _client(AutoModel())
    _sorted_big(client)
    _write(big_vault, "notes/n000.md", "Changed body.")
    _write(big_vault, "notes/evergreen/ev2.md", "Agents need memory.")
    _rescan(big_vault)
    # ev gets one call for the 4 new claims; the new ev2 is matched against all 170 claims.
    failing = AutoModel(fail_when=lambda kind, n: kind == "agree" and n == 2)
    assert _run(_client(failing))["status"] == "failed"
    assert failing.kinds() == ["extract", "agree", "agree", "agree"]
    assert sorted(_claims_sent(failing.users("agree")[0])) == N000_V2
    assert len(_claims_sent(failing.users("agree")[1])) == 150
    healthy = AutoModel()
    view = _run(_client(healthy))["view"]
    assert healthy.kinds() == ["agree", "confirm", "confirm", "stances", "summary"]
    assert len(_claims_sent(healthy.users("agree")[0])) == 20
    assert healthy.users("agree")[0].startswith("EVERGREEN\nev2 | Agents need memory.\n")
    assert [s["evergreen"]["path"] for s in view["shared"]] == [EVERGREEN, "notes/evergreen/ev2.md"]
    assert [len(s["claims"]) for s in view["shared"]] == [TOTAL_BIG, TOTAL_BIG]
    assert view["stale"] is False


def test_an_edited_evergreen_is_matched_again_alone(big_vault: Path) -> None:
    _write(big_vault, "notes/evergreen/ev2.md", "Agents need memory.")
    _rescan(big_vault)
    model = AutoModel()
    client = _client(model)
    _sorted_big(client)
    _write(big_vault, EVERGREEN, "Agents need plans.")
    _rescan(big_vault)
    assert _view(client)["next_step"] == "analyse"
    model.calls.clear()
    view = _run(client)["view"]
    assert model.kinds() == ["agree", "agree", "confirm"]
    assert [u.split("\n\nCLAIMS\n")[0] for u in model.users("agree")] == [
        "EVERGREEN\nev | Agents need plans."
    ] * 2
    assert (view["stale"], len(view["shared"])) == (False, 2)


def test_a_touched_evergreen_with_the_same_text_costs_no_call(big_vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    before = _sorted_big(client)
    stat = (big_vault / EVERGREEN).stat()
    os.utime(big_vault / EVERGREEN, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    _rescan(big_vault)
    assert _view(client)["next_step"] == "analyse"
    model.calls.clear()
    assert _run(client)["view"] == before
    assert model.calls == []


def test_a_removed_evergreen_drops_its_shared_point_without_a_call(big_vault: Path) -> None:
    _write(big_vault, "notes/evergreen/ev2.md", "Agents need memory.")
    _rescan(big_vault)
    model = AutoModel()
    client = _client(model)
    assert len(_sorted_big(client)["shared"]) == 2
    (big_vault / "notes/evergreen/ev2.md").unlink()
    _rescan(big_vault)
    model.calls.clear()
    view = _run(client)["view"]
    assert model.calls == []
    assert [s["evergreen"]["path"] for s in view["shared"]] == [EVERGREEN]


def test_the_summary_is_kept_while_the_sides_stay_the_same(big_vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    before = _sorted_big(client)
    _write(big_vault, "notes/neutral.md", "## Key Claims\n- Weather is mild.")
    _rescan(big_vault)
    model.calls.clear()
    view = _run(client)["view"]
    assert model.kinds() == ["agree", "confirm", "stances"]
    assert _claims_sent(model.users("stances")[0]) == ["Weather is mild."]
    assert (view["summary"], view["unrelated"]) == (before["summary"], before["unrelated"] + 1)
    assert view["next_step"] == "done"


def test_a_summary_saved_without_its_sides_is_redone(big_vault: Path) -> None:
    client = _client(AutoModel())
    _sorted_big(client)
    with duckdb.connect(str(_db(big_vault))) as con:
        sig, payload = con.execute(
            "SELECT signature, payload FROM claim_analysis WHERE kind = ?", [SUMMARY_KIND]
        ).fetchone()
        old = json.loads(payload)
        del old["sides"]
        save_analysis(con, "big", SUMMARY_KIND, "Q?", "older", old)
    model = AutoModel()
    _run(_client(model))
    assert model.kinds() == ["summary"]
    assert sig != "older"


def _agreement_rows(db: Path) -> dict[str, tuple[str, Any]]:
    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT kind, signature, payload FROM claim_analysis WHERE kind LIKE 'agreement%'"
        ).fetchall()
    return {kind: (sig, json.loads(payload)) for kind, sig, payload in rows}


def _as_legacy(db: Path, chunks: int) -> list[str]:
    """Rewrite the saved agreement the way a finished run before #175 left it."""
    rows = _agreement_rows(db)
    shared = rows[AGREEMENT_KIND][1]
    kept = shared[0]["claim_ids"]
    with duckdb.connect(str(db)) as con:
        ids = [cid for (cid,) in con.execute("SELECT claim_id FROM note_claims").fetchall()]
        con.execute("DELETE FROM claim_analysis WHERE kind LIKE 'agreement%'")
        sig = signature(ids)
        save_analysis(con, "big", AGREEMENT_KIND, "", sig, shared)
        state = {
            "chunks": chunks,
            "found": {EVERGREEN: {"text": "Old point.", "best": len(kept), "claim_ids": kept}},
            "confirmed": [EVERGREEN],
        }
        save_analysis(con, "big", AGREEMENT_PROGRESS_KIND, "", sig, state)
    return list(kept)


def test_a_finished_agreement_from_before_is_carried_over(big_vault: Path) -> None:
    client = _client(AutoModel())
    _finish_reading(client)
    kept = _as_legacy(_db(big_vault), chunks=2)
    assert _view(client)["next_step"] == "analyse"
    model = AutoModel()
    view = _run(_client(model))["view"]
    assert model.calls == []
    entry = _agreement_rows(_db(big_vault))[AGREEMENT_STATE_KIND][1]["evergreens"][EVERGREEN]
    assert (entry["kept"], entry["claim_ids"], entry["confirmed"]) == (kept, kept, True)
    assert view["shared"][0]["text"] == "Old point."
    _write(big_vault, "notes/n000.md", "Changed body.")
    _rescan(big_vault)
    _run(_client(model))
    assert model.kinds() == ["extract", "agree", "confirm"]


def test_an_unfinished_agreement_from_before_is_matched_again(big_vault: Path) -> None:
    client = _client(AutoModel())
    _finish_reading(client)
    _as_legacy(_db(big_vault), chunks=1)
    model = AutoModel()
    _run(_client(model))
    assert model.kinds() == ["agree", "agree", "confirm"]


def test_proposals_from_before_get_their_claim_ids_stored(big_vault: Path) -> None:
    client = _client(AutoModel())
    _finish_reading(client)
    with duckdb.connect(str(_db(big_vault))) as con:
        con.execute("DELETE FROM claim_analysis WHERE kind = ?", [QUESTIONS_BASE_KIND])
        ids = sorted(cid for (cid,) in con.execute("SELECT claim_id FROM note_claims").fetchall())
    model = AutoModel()
    _run(_client(model))
    assert model.calls == []
    with duckdb.connect(str(_db(big_vault)), read_only=True) as con:
        row = con.execute(
            "SELECT signature, payload FROM claim_analysis WHERE kind = ?", [QUESTIONS_BASE_KIND]
        ).fetchone()
    assert row == (signature(ids), json.dumps(ids))
    _write(big_vault, "notes/n000.md", "Changed body.")
    _rescan(big_vault)
    _run(_client(model))
    assert model.kinds() == ["extract", "agree", "confirm"]


def test_unreadable_agreement_answer_fails_and_keeps_earlier_chunks(big_vault: Path) -> None:
    model = AutoModel(raw={("agree", 1): '{"point": "P", "text": "cut'})
    client = _client(model)
    body = _run(client)
    while body["view"]["pending_notes"]:
        body = _run(client)
    assert (body["status"], body["message"]) == (
        "failed",
        "The model's answer could not be read. Try again.",
    )
    assert body["view"]["shared"] == []
    healthy = AutoModel()
    final = _run(_client(healthy))
    assert final["status"] == "done"
    assert healthy.kinds() == ["agree", "confirm"]
    assert len(final["view"]["shared"][0]["claims"]) == TOTAL_BIG


def test_an_evergreen_is_cut_to_300_characters_in_the_prompt(vault: Path) -> None:
    _write(vault, EVERGREEN, "e" * 500)
    _rescan(vault)
    model = AutoModel()
    _run(_client(model))
    line = model.users("agree")[0].split("EVERGREEN\n")[1].split("\n\nCLAIMS\n")[0]
    assert line == "ev | " + "e" * 300


def test_a_duplicate_claim_in_one_note_is_stored_once(vault: Path) -> None:
    twice = {"claims": [{"note": A, "text": "Same."}, {"note": A, "text": "Same."}]}
    model = AutoModel(raw={("extract", 0): twice})
    view = _run(_client(model))["view"]
    with duckdb.connect(str(_db(vault)), read_only=True) as con:
        rows = con.execute("SELECT text FROM note_claims WHERE path = ?", [A]).fetchall()
    assert rows == [("Same.",)]
    assert view["pending_notes"] == 0


def test_claim_ids_are_12_hex_characters_and_depend_on_note_and_text() -> None:
    first = claim_id(A, "text")
    assert len(first) == 12
    assert int(first, 16) >= 0
    assert first == claim_id(A, "text")
    assert first != claim_id(B, "text")
    assert first != claim_id(A, "other")


def test_unlinked_supporters_leaves_out_linkers_and_the_evergreen_itself() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE links (source_path VARCHAR, target_path VARCHAR)")
    con.execute("INSERT INTO links VALUES (?, ?), (?, ?)", [A, EVERGREEN, B, "other.md"])
    assert unlinked_supporters(con, EVERGREEN, [A, B, KEY, EVERGREEN, B]) == [B, KEY]


def test_a_cached_shared_claim_backed_by_one_note_is_not_shown(vault: Path) -> None:
    client = _client(AutoModel())
    view = _run(client)["view"]
    claims = view["shared"][0]["claims"]
    one_note = [c["id"] for c in claims if c["path"] == A][:2]
    assert len(one_note) == 2
    with duckdb.connect(str(_db(vault))) as con:
        (sig,) = con.execute(
            "SELECT signature FROM claim_analysis WHERE kind = ?", [AGREEMENT_KIND]
        ).fetchone()
        payload = [{"text": "one note", "claim_ids": one_note, "evergreen": EVERGREEN}]
        save_analysis(con, "big", AGREEMENT_KIND, "", sig, payload)
    assert _view(client)["shared"] == []


# --- endpoints ---------------------------------------------------------------------


def test_endpoints_404_for_unknown_topics_and_before_a_scan(vault: Path) -> None:
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


def test_a_300_character_question_is_accepted(vault: Path) -> None:
    response = _client(None).put("/topics/big/claims/question", json={"question": "x" * 300})
    assert response.status_code == 200
    assert response.json()["question"] == "x" * 300


def test_run_request_defaults_to_not_approved(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "0.0001")
    assert _client(AutoModel()).post("/topics/big/claims/run", json={}).json()["status"] == (
        "needs_approval"
    )


# --- chat tool (C4, C5) -----------------------------------------------------------


def test_topic_claims_tool_returns_the_cached_view_without_a_model(vault: Path) -> None:
    view = _run(_client(AutoModel()))["view"]
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
    client = _client(AutoModel())
    _run(client)
    (vault / ".kai" / "topics.yaml").write_text(
        TOPICS_YAML.replace("[notes/reflections]", "[notes/reflections, notes/key.md]"),
        encoding="utf-8",
    )
    view = _view(client)
    assert view["claim_count"] == 8
    assert KEY not in json.dumps(view)
