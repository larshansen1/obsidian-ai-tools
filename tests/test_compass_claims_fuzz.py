"""Fuzz the claims run with malformed model answers.

A real model sometimes answers with the wrong type, a missing key, a huge number or cut-off JSON.
Whatever it sends, the run must end with one of the known statuses (never a 500), and what is
stored must stay consistent. Seeded, so a failure repeats: run one seed with
`pytest -k "seed_17"`. More seeds: COMPASS_FUZZ_SEEDS=500.
"""

import json
import os
import random
import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from vault_compass.ai_client import ModelEvent, TextDelta, Usage
from vault_compass.app import create_app
from vault_compass.claims_run import (
    AGREEMENT_SYSTEM,
    EXTRACT_SYSTEM,
    QUESTIONS_SYSTEM,
    STANCE_SYSTEM,
)
from vault_compass.config import CompassSettings
from vault_compass.notes import refresh_notes
from vault_compass.topics import load_topics

SEEDS = int(os.environ.get("COMPASS_FUZZ_SEEDS", "60"))
STATUSES = {"done", "needs_approval", "not_configured", "failed"}
KIND = {
    EXTRACT_SYSTEM: "extract",
    QUESTIONS_SYSTEM: "questions",
    AGREEMENT_SYSTEM: "agree",
    STANCE_SYSTEM: "stances",
}
TOPICS_YAML = (
    "topics:\n  big:\n    name: Big\n    tags: [x]\nmin_notes: 1\n"
    "trend_start: 2026-04-01\nai_exclude_folders: [notes/reflections]\n"
)
PATHS = [f"notes/n{i}.md" for i in range(9)]


def junk(rng: random.Random, depth: int = 0) -> Any:
    """Any JSON value: the wrong type for whatever field it is put in."""
    leaves: list[Any] = [
        None,
        True,
        False,
        0,
        -1,
        1,
        2,
        10**30,
        1.5,
        -0.0,
        "",
        " ",
        "x" * 500,
        "SPU",
        "é\u0000\n",
        "notes/n1.md",
    ]
    if depth > 2 or rng.random() < 0.5:
        return rng.choice(leaves)
    if rng.random() < 0.5:
        return [junk(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {rng.choice(["s", "e", "c", "text", "note", "claims"]): junk(rng, depth + 1)}


def number_list(rng: random.Random) -> Any:
    return rng.choice([[rng.randint(-2, 12) for _ in range(rng.randint(0, 8))], junk(rng)])


def shaped(kind: str, rng: random.Random) -> Any:
    if kind == "extract":
        item = lambda: {  # noqa: E731
            "note": rng.choice([*PATHS, junk(rng)]),
            "text": rng.choice(["A claim.", "A claim.", junk(rng)]),
        }
        return {"claims": rng.choice([[item() for _ in range(rng.randint(0, 6))], junk(rng)])}
    if kind == "questions":
        return {"questions": rng.choice([["Q one?", junk(rng), "Q one?"], junk(rng)])}
    if kind == "agree":
        item = lambda: {  # noqa: E731
            "e": rng.choice([1, 2, 3, junk(rng)]),
            "text": rng.choice(["Shared.", junk(rng)]),
            "c": number_list(rng),
        }
        return {"matches": rng.choice([[item() for _ in range(rng.randint(0, 4))], junk(rng)])}
    return {
        "s": rng.choice(["".join(rng.choices("SPUspu ,x!\n", k=rng.randint(0, 40))), junk(rng)])
    }


def answer(kind: str, rng: random.Random) -> str:
    mode = rng.choices(["shaped", "junk", "raw", "empty", "truncated"], [8, 3, 2, 1, 2])[0]
    if mode == "shaped":
        return json.dumps(shaped(kind, rng))
    if mode == "junk":
        return json.dumps(junk(rng))
    if mode == "raw":
        return rng.choice(["not json", "{", "}{", '{"a":', "[]", "{} {}", "```json\n{}\n```"])
    if mode == "empty":
        return rng.choice(["", "  \n"])
    return json.dumps(shaped(kind, rng))[: rng.randint(0, 30)]


class FuzzModel:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ModelEvent]:
        kind = KIND[messages[0]["content"]]
        if self.rng.random() < 0.05:
            raise RuntimeError("provider down")
        text = answer(kind, self.rng)
        if kind == "extract" and self.rng.random() < 0.5:
            paths = re.findall(r"^### NOTE (\S+) \(", messages[1]["content"], re.MULTILINE)
            text = json.dumps(
                {"claims": [{"note": p, "text": f"{p} says {i}"} for p in paths for i in range(2)]}
            )
        yield TextDelta(text)
        yield Usage(model="m/x", input_tokens=10, output_tokens=5, cost_usd=0.001)


@pytest.fixture(scope="module")
def base_db(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("fuzz") / "vault"
    for rel in PATHS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: t\ncreated: 2026-09-01\ntags: [x]\n---\nBody {rel}.\n")
    ev = root / "notes" / "evergreen" / "ev.md"
    ev.parent.mkdir(parents=True)
    ev.write_text("---\ntitle: ev\ncreated: 2026-09-01\ntags: [x]\n---\nPoint.\n")
    (root / ".kai").mkdir()
    (root / ".kai" / "topics.yaml").write_text(TOPICS_YAML)
    db = root / ".kai" / "base.duckdb"
    refresh_notes(root, db, load_topics(root / ".kai" / "topics.yaml"))
    return root, db


def _check(view: dict[str, Any]) -> None:
    sides = len(view["supporting"]) + len(view["pushing_back"]) + view["unrelated"]
    assert sides in (0, view["claim_count"])
    assert view["pending_notes"] >= 0
    assert view["read_notes"] + view["pending_notes"] == len(PATHS)
    assert view["next_step"] in {"read", "analyse", "sort", "done"}
    assert (view["next_step"] != "done") == view["stale"]
    assert len(view["questions"]) <= 3
    for shared in view["shared"]:
        assert len({c["path"] for c in shared["claims"]}) >= 2
        if shared["evergreen"]:
            assert shared["evergreen"]["path"] == "notes/evergreen/ev.md"
    ids = [c["id"] for c in view["supporting"] + view["pushing_back"]]
    assert len(ids) == len(set(ids))
    for claim in view["supporting"] + view["pushing_back"]:
        assert claim["path"] in PATHS
        assert claim["text"]


def _check_db(db: Path) -> None:
    with duckdb.connect(str(db), read_only=True) as con:
        found = con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        tables = {name for (name,) in found}
        if "note_claims" not in tables:
            return  # nothing was saved yet: every call so far failed
        rows = con.execute("SELECT claim_id, path, text FROM note_claims").fetchall()
        assert all(path in PATHS and text.strip() for _cid, path, text in rows)
        assert len({cid for cid, _p, _t in rows}) == len(rows)
        for kind, payload in con.execute("SELECT kind, payload FROM claim_analysis").fetchall():
            data = json.loads(payload)
            assert isinstance(
                data, dict if kind.endswith("progress") or kind == "stances" else list
            )


@pytest.mark.parametrize("seed", range(SEEDS), ids=lambda s: f"seed_{s}")
def test_malformed_model_answers_never_crash_the_run(
    seed: int, base_db: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, base = base_db
    db = tmp_path / "compass.duckdb"
    shutil.copy(base, db)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(root))
    monkeypatch.setenv("COMPASS_DB_PATH", str(db))
    monkeypatch.setenv("COMPASS_AI_ACTION_LIMIT_USD", "100")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    rng = random.Random(seed)
    model = FuzzModel(rng)
    client = TestClient(create_app(CompassSettings(), model_factory=lambda _s: model))
    for step in range(8):
        if step == 3:
            put = client.put("/topics/big/claims/question", json={"question": "Q?"})
            assert put.status_code == 200
        response = client.post("/topics/big/claims/run", json={"approved": rng.random() < 0.3})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] in STATUSES
        if body["status"] != "done":
            assert body["message"]
            assert not body["message"].startswith("Unexpected error"), body["message"]
        _check(body["view"])
        view = client.get("/topics/big/claims")
        assert view.status_code == 200
        _check(view.json())
        _check_db(db)
