"""Usage log: screen views, actions and AI calls, written to compass.duckdb (D6).

Same idea as kai's observability tables, so usage and cost can be queried
with plain SQL. The table is never touched by a notes refresh.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .db import writable

UsageKind = Literal["screen_view", "action", "ai_call"]

_USAGE_DDL = """
CREATE TABLE IF NOT EXISTS usage_events (
    ts TIMESTAMP NOT NULL,
    kind VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    detail VARCHAR,
    model VARCHAR,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd DOUBLE
)
"""


def log_usage(
    db_path: Path,
    kind: UsageKind,
    name: str,
    *,
    detail: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    now: datetime | None = None,
) -> None:
    """Append one event. `ts` is UTC; pass `now` for a fixed clock in tests."""
    ts = (now or datetime.now(UTC)).replace(tzinfo=None)
    with writable(db_path) as con:
        con.execute(_USAGE_DDL)
        con.execute(
            "INSERT INTO usage_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ts, kind, name, detail, model, input_tokens, output_tokens, cost_usd],
        )
