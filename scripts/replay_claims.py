"""Run the claims flow for one topic against a COPY of your real compass.duckdb.

Use it before calling a claims change done: it exercises your real notes and the real model,
the way the screen does, without touching the live database.

    uv run python scripts/replay_claims.py neurodiversity          # real model, costs a few cents
    uv run python scripts/replay_claims.py neurodiversity --dry    # no model: shows what is cached

It copies `compass_db_path` to a temp folder, runs the topic (approving any cost question), then
runs it a second time to check that nothing is repeated. Exit code 1 if a run did not finish.
"""

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from vault_compass.app import create_app
from vault_compass.config import get_compass_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("topic")
    parser.add_argument("--dry", action="store_true", help="only show the cached state")
    args = parser.parse_args()

    live = get_compass_settings()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "compass.duckdb"
        shutil.copy(live.compass_db_path, copy)
        settings = live.model_copy(update={"compass_db_path": copy})
        client = TestClient(create_app(settings), raise_server_exceptions=True)
        before = client.get(f"/topics/{args.topic}/claims").json()
        print(
            f"before: next={before['next_step']} claims={before['claim_count']} "  # noqa: T201
            f"question={before['question']!r}"
        )
        if args.dry:
            return 0
        outcomes = []
        for label in ("run", "second run"):
            started = time.time()
            body = client.post(f"/topics/{args.topic}/claims/run", json={"approved": True}).json()
            view = body["view"]
            print(
                f"{label}: {body['status']} {body['message'] or ''} ({time.time() - started:.0f}s) "  # noqa: T201
                f"next={view['next_step']} supporting={len(view['supporting'])} "
                f"pushing_back={len(view['pushing_back'])} unrelated={view['unrelated']} "
                f"shared={len(view['shared'])}"
            )
            outcomes.append(body["status"])
        return 0 if outcomes == ["done", "done"] else 1


if __name__ == "__main__":
    sys.exit(main())
