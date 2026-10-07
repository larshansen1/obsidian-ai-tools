"""Chat history, one thread per topic (C9), stored in compass.duckdb.

The browser saves the assistant-ui thread export as opaque JSON; the server
never reads inside it. The table is never touched by a notes refresh.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .db import readonly, writable

# The thread used on screens that have no topic (the topic map).
GLOBAL_KEY = "_global"

_DDL = """
CREATE TABLE IF NOT EXISTS chat_history (
    thread_key VARCHAR PRIMARY KEY,
    updated TIMESTAMP NOT NULL,
    messages VARCHAR NOT NULL
)
"""


def load_history(db_path: Path, key: str) -> Any | None:
    if not db_path.exists():
        return None
    with readonly(db_path) as con:
        try:
            row = con.execute(
                "SELECT messages FROM chat_history WHERE thread_key = ?", [key]
            ).fetchone()
        except duckdb.CatalogException:
            return None
    return json.loads(row[0]) if row else None


def save_history(db_path: Path, key: str, messages: Any, now: datetime | None = None) -> None:
    ts = (now or datetime.now(UTC)).replace(tzinfo=None)
    with writable(db_path) as con:
        con.execute(_DDL)
        con.execute(
            "INSERT OR REPLACE INTO chat_history VALUES (?, ?, ?)",
            [key, ts, json.dumps(messages)],
        )
