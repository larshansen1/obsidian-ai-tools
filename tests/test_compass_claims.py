"""Tests for claims across sources and where they agree (T3, T4, Q6, Q7, N4)."""

import json
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
    ClaimItem,
    claim_id,
    save_analysis,
    signature,
    unlinked_supporters,
)
from vault_compass.claims_run import (
    AGREEMENT_SYSTEM,
    CHUNK_CLAIMS,
    EXTRACT_SYSTEM,
    MAX_EVERGREENS,
    QUESTIONS_SYSTEM,
    STANCE_SYSTEM,
    _chunks,
    _numbered,
    _sample,
    finalize_shared,
    key_claims,
    merge_matches,
    parse_extracted,
    parse_json_object,
    parse_matches,
    parse_questions,
    parse_stances,
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


Kind = Literal["extract", "questions", "agree", "stances"]
_KIND_OF = {
    EXTRACT_SYSTEM: "extract",
    QUESTIONS_SYSTEM: "questions",
    AGREEMENT_SYSTEM: "agree",
    STANCE_SYSTEM: "stances",
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
            return {"questions": ["Q one?", "Q two?"]}
        if kind == "agree":
            lines = user.split("\n\nEVERGREENS\n")[0].split("\n")[1:]
            return {
                "matches": [
                    {
                        "e": 1,
                        "text": f"Shared point {len(lines)}",
                        "c": list(range(1, len(lines) + 1)),
                    }
                ]
            }
        question, lines = user.split("\n\nCLAIMS\n")
        texts = [line.split(". ", 1)[1] for line in lines.split("\n")]
        return {"s": self.stance(question.removeprefix("QUESTION\n"), texts)}


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


def test_parse_questions_keeps_three_unique_non_blank() -> None:
    data = {"questions": ["Q1", " Q1 ", "", 3, "Q2", "Q3", "Q4"]}
    assert parse_questions(data) == ["Q1", "Q2", "Q3"]


def _item(i: int, path: str = A) -> ClaimItem:
    return ClaimItem(id=f"id{i}", text="t", path=path, title="x")


def _evergreens(n: int) -> list[NoteRef]:
    return [NoteRef(path=f"e{i}.md", title=f"E{i}", created=None) for i in range(1, n + 1)]


def test_parse_matches_maps_numbers_to_evergreen_paths_and_claim_ids() -> None:
    chunk = [_item(i) for i in range(1, 5)]
    data = {"matches": [{"e": 2, "text": " Shared. ", "c": [1, 3, 3]}]}
    assert parse_matches(data, chunk, _evergreens(3)) == [("e2.md", "Shared.", ["id1", "id3"])]


@pytest.mark.parametrize("e", [0, 4, -1, True, "1", 1.5, None])
def test_parse_matches_drops_evergreen_numbers_outside_the_list(e: Any) -> None:
    data = {"matches": [{"e": e, "text": "t", "c": [1]}]}
    assert parse_matches(data, [_item(1)], _evergreens(3)) == []


def test_parse_matches_keeps_only_claim_numbers_in_range() -> None:
    chunk = [_item(1), _item(2)]
    data = {"matches": [{"e": 1, "text": "t", "c": [0, 1, 2, 3, True, "2", 2.0]}]}
    assert parse_matches(data, chunk, _evergreens(1)) == [("e1.md", "t", ["id1", "id2"])]


@pytest.mark.parametrize(
    "item",
    [
        {"e": 1, "text": "", "c": [1]},
        {"e": 1, "text": "t", "c": []},
        {"e": 1, "text": 3, "c": [1]},
        {"e": 1, "text": "t"},
        "junk",
        None,
    ],
)
def test_parse_matches_skips_incomplete_items(item: Any) -> None:
    assert parse_matches({"matches": [item]}, [_item(1)], _evergreens(1)) == []


@pytest.mark.parametrize("data", [{}, {"matches": None}, {"matches": []}])
def test_parse_matches_handles_a_missing_list(data: dict[str, Any]) -> None:
    assert parse_matches(data, [_item(1)], _evergreens(1)) == []


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


def test_parse_stances_reads_one_letter_per_claim() -> None:
    chunk = [_item(i) for i in range(5)]
    assert parse_stances({"s": "SPUsp"}, chunk) == {
        "id0": "supporting",
        "id1": "pushing_back",
        "id2": "unrelated",
        "id3": "supporting",
        "id4": "pushing_back",
    }


def test_parse_stances_ignores_spaces_and_stray_characters() -> None:
    chunk = [_item(i) for i in range(3)]
    assert parse_stances({"s": "S, P x\nU!"}, chunk) == {
        "id0": "supporting",
        "id1": "pushing_back",
        "id2": "unrelated",
    }


def test_parse_stances_treats_a_short_or_missing_answer_as_unrelated() -> None:
    chunk = [_item(i) for i in range(3)]
    assert parse_stances({"s": "S"}, chunk) == {
        "id0": "supporting",
        "id1": "unrelated",
        "id2": "unrelated",
    }
    assert parse_stances({"s": 5}, chunk[:1]) == {"id0": "unrelated"}
    assert parse_stances({}, chunk[:1]) == {"id0": "unrelated"}


def test_parse_stances_ignores_letters_past_the_last_claim() -> None:
    assert parse_stances({"s": "SSSS"}, [_item(0)]) == {"id0": "supporting"}


def test_the_prompts_ask_for_yes_or_no_questions_and_every_matching_claim() -> None:
    assert "answerable with yes or no" in QUESTIONS_SYSTEM
    assert "Which, What, How or Why" in QUESTIONS_SYSTEM
    assert "Include every claim that matches" in AGREEMENT_SYSTEM
    assert "supports a yes" in STANCE_SYSTEM


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


@pytest.mark.parametrize(("count", "kept"), [(0, 0), (1, 1), (150, 150), (151, 76), (1138, 143)])
def test_sample_never_exceeds_150_claims_and_spreads_over_all(count: int, kept: int) -> None:
    claims = [_item(i) for i in range(count)]
    sample = _sample(claims)
    assert len(sample) == kept
    if count > 150:
        assert sample[0] is claims[0]
        assert claims.index(sample[-1]) > count - 2 * (count // kept)


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
        "shared": [],
        "stale": True,
        "next_step": "read",
    }


def test_first_run_reads_proposes_and_finds_shared_claims(vault: Path) -> None:
    model = AutoModel()
    body = _run(_client(model))
    assert (body["status"], body["message"], body["estimate_usd"]) == ("done", None, None)
    assert model.kinds() == ["extract", "questions", "agree"]
    view = body["view"]
    assert (view["read_notes"], view["pending_notes"], view["claim_count"]) == (3, 0, 10)
    assert view["questions"] == ["Q one?", "Q two?"]
    assert (view["question"], view["stale"]) == (None, False)
    assert len(view["shared"]) == 1
    shared = view["shared"][0]
    assert shared["text"] == "Shared point 10"
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
    assert model.kinds() == ["extract", "questions", "agree"]
    assert re.findall(r"^### NOTE (\S+)", model.users("extract")[0], re.MULTILINE) == [A]
    assert (fresh["claim_count"], fresh["stale"], fresh["pending_notes"]) == (10, False, 0)
    assert fresh["questions"] == ["Q one?", "Q two?"]


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
    assert model.kinds() == ["stances"]
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
    assert view["questions"] == ["Q one?", "Q two?"]


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
    assert model.kinds() == ["stances"]
    model.calls.clear()
    client.put("/topics/big/claims/question", json={"question": "First?"})
    assert _run(client)["view"] == first
    assert model.calls == []


def test_a_stance_answer_with_no_letters_leaves_every_claim_unrelated(vault: Path) -> None:
    model = AutoModel(raw={("stances", 0): {"s": ""}})
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


def test_at_most_forty_evergreens_are_offered(vault: Path) -> None:
    assert MAX_EVERGREENS == 40
    for i in range(45):
        _write(vault, f"notes/evergreen/z{i:02}.md", f"Idea {i}.")
    _rescan(vault)
    model = AutoModel()
    _run(_client(model))
    block = model.users("agree")[0].split("EVERGREENS\n")[1].split("\n")
    assert len(block) == 40
    assert block[0].startswith("1. ev | Agents need plans and tools.")
    assert block[-1].startswith("40. z38 | Idea 38.")


def test_an_evergreen_number_outside_the_list_is_ignored(vault: Path) -> None:
    model = AutoModel(raw={("agree", 0): {"matches": [{"e": 2, "text": "t", "c": [1, 2]}]}})
    view = _run(_client(model))["view"]
    assert (view["shared"], view["stale"]) == ([], False)


def test_a_match_backed_by_one_note_is_not_shared(vault: Path) -> None:
    model = AutoModel(raw={("agree", 0): {"matches": [{"e": 1, "text": "t", "c": [1]}]}})
    assert _run(_client(model))["view"]["shared"] == []


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
    assert model.kinds() == ["extract", "questions", "agree"]


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


def test_claims_calls_turn_thinking_off_and_get_a_bigger_output_budget(vault: Path) -> None:
    model = AutoModel()
    _run(_client(model))
    (settings,) = model.settings
    assert settings.compass_ai_reasoning is False
    assert settings.compass_ai_max_output_tokens == 4000


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
    assert model.kinds() == ["extract", "extract", "extract", "questions", "agree"]
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
    assert parse_questions({"questions": bad}) == []
    assert parse_matches({"matches": bad}, chunk, _evergreens(1)) == []
    assert (
        parse_matches({"matches": [{"e": 1, "text": "t", "c": bad}]}, chunk, _evergreens(1)) == []
    )
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
    assert second["questions"] == ["Q one?", "Q two?"]
    assert model.kinds()[-2:] == ["questions", "agree"]


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
    sizes = [len(u.split("\n\nEVERGREENS\n")[0].split("\n")) - 1 for u in model.users("agree")]
    assert sizes == [150, 20]
    shared = view["shared"]
    assert len(shared) == 1
    assert shared[0]["text"] == "Shared point 150"
    assert len(shared[0]["claims"]) == TOTAL_BIG
    assert len({c["id"] for c in shared[0]["claims"]}) == TOTAL_BIG
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    model.calls.clear()
    sorted_view = _run(client)["view"]
    lines = [len(u.split("\n\nCLAIMS\n")[1].split("\n")) for u in model.users("stances")]
    assert lines == [150, 20]
    assert len(sorted_view["supporting"]) == 43  # 42 "claim 1" + "Tools help."
    assert len(sorted_view["pushing_back"]) == 43  # 42 "claim 2" + "Plans fail."
    assert sorted_view["unrelated"] == 84  # the "claim 3" and "claim 4" of 42 notes


def test_the_questions_prompt_samples_across_all_claims(big_vault: Path) -> None:
    model = AutoModel()
    _finish_reading(_client(model))
    lines = model.users("questions")[0].split("\n")
    assert len(lines) == 85  # 170 claims, every second one
    assert lines[0].startswith("1 | ")
    paths = {line.split(" | ")[2].split(" claim")[0] for line in lines if " claim " in line}
    assert "notes/a.md" in paths
    assert "notes/n039.md" in paths


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

    healthy = AutoModel()
    final = _run(_client(healthy))["view"]
    assert healthy.kinds() == ["stances"]
    resumed = healthy.users("stances")[0].split("\n\nCLAIMS\n")[1].split("\n")
    assert len(resumed) == 20
    assert not set(first_chunk_lines) & set(resumed)
    assert final["stale"] is False
    assert len(final["supporting"]) + len(final["pushing_back"]) + final["unrelated"] == TOTAL_BIG
    assert _run(_client(None))["view"] == final


def test_a_stopped_agreement_resumes_at_the_chunk_it_stopped_on(big_vault: Path) -> None:
    failing = AutoModel(fail_when=lambda kind, n: kind == "agree" and n == 1)
    client = _client(failing)
    _finish_reading(client)  # extraction and questions complete, agreement stops
    view = _view(client)
    assert (view["shared"], view["stale"], view["claim_count"]) == ([], True, TOTAL_BIG)
    assert failing.kinds().count("agree") == 2
    healthy = AutoModel()
    final = _run(_client(healthy))["view"]
    assert healthy.kinds() == ["agree"]
    assert healthy.users("agree")[0].split("\n\nEVERGREENS\n")[0].count("\n") == 20
    assert final["stale"] is False
    assert len(final["shared"][0]["claims"]) == TOTAL_BIG
    assert final["shared"][0]["text"] == "Shared point 150"


def test_changed_notes_restart_the_analysis_not_the_reading(big_vault: Path) -> None:
    model = AutoModel()
    client = _client(model)
    _finish_reading(client)
    client.put("/topics/big/claims/question", json={"question": "Q?"})
    _run(client)
    _write(big_vault, "notes/n000.md", "Changed body.")
    _rescan(big_vault)
    model.calls.clear()
    view = _run(client)["view"]
    assert model.kinds() == ["extract", "questions", "agree", "agree", "stances", "stances"]
    assert view["claim_count"] == TOTAL_BIG
    assert view["stale"] is False


def test_unreadable_agreement_answer_fails_and_keeps_earlier_chunks(big_vault: Path) -> None:
    model = AutoModel(raw={("agree", 1): '{"matches": [{"e": 1, "text": "cut'})
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
    assert healthy.kinds() == ["agree"]
    assert len(final["view"]["shared"][0]["claims"]) == TOTAL_BIG


def test_an_evergreen_is_cut_to_300_characters_in_the_prompt(vault: Path) -> None:
    _write(vault, EVERGREEN, "e" * 500)
    _rescan(vault)
    model = AutoModel()
    _run(_client(model))
    line = model.users("agree")[0].split("EVERGREENS\n")[1]
    assert line == "1. ev | " + "e" * 300


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
    sig = signature(c["id"] for c in claims)
    with duckdb.connect(str(_db(vault))) as con:
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
