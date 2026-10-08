"""Cost limits for AI actions (N3).

Spend comes from the `ai_call` rows in the usage log. A call is estimated
before it runs; when the estimate would take the action or the month over its
limit, the chat asks first.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .ai_client import Usage
from .config import CompassSettings
from .db import readonly
from .usage import log_usage

CHARS_PER_TOKEN = 4

_MONTH_SPEND_SQL = """
SELECT COALESCE(SUM(cost_usd), 0) FROM usage_events
WHERE kind = 'ai_call' AND ts >= ? AND ts < ?
"""


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def month_spend(db_path: Path, now: datetime | None = None) -> float:
    """USD spent on AI calls in the (UTC) calendar month of `now`."""
    if not db_path.exists():
        return 0.0
    start, end = _month_bounds(now or datetime.now(UTC))
    with readonly(db_path) as con:
        try:
            row = con.execute(_MONTH_SPEND_SQL, [start, end]).fetchone()
        except duckdb.CatalogException:
            return 0.0  # no AI call has been logged yet
    return float(row[0]) if row else 0.0


def estimate_cost(settings: CompassSettings, input_chars: int) -> float:
    """Worst-case USD for one model call: the input so far plus a full-length reply."""
    input_tokens = input_chars / CHARS_PER_TOKEN
    return (
        input_tokens * settings.compass_ai_input_usd_per_mtok
        + settings.compass_ai_max_output_tokens * settings.compass_ai_output_usd_per_mtok
    ) / 1_000_000


def _usd(value: float) -> str:
    # Tiny limits would all read "$0.00" with two decimals.
    return f"${value:.2f}" if value >= 0.01 else f"${value:.4f}"


@dataclass(frozen=True)
class LimitCheck:
    ok: bool
    reason: str | None


def check_limits(
    settings: CompassSettings, *, action_cost: float, month_cost: float, next_call: float
) -> LimitCheck:
    """`action_cost` is spent so far in this action; `month_cost` before this action."""
    action_total = action_cost + next_call
    if action_total > settings.compass_ai_action_limit_usd:
        return LimitCheck(
            False,
            f"This action may cost up to {_usd(action_total)}, over the "
            f"{_usd(settings.compass_ai_action_limit_usd)} per-action limit.",
        )
    month_total = month_cost + action_total
    if month_total > settings.compass_ai_monthly_limit_usd:
        return LimitCheck(
            False,
            f"This would bring this month's AI spend to {_usd(month_total)}, over the "
            f"{_usd(settings.compass_ai_monthly_limit_usd)} monthly limit.",
        )
    return LimitCheck(True, None)


def record_call(settings: CompassSettings, usage: Usage, name: str, detail: str | None) -> float:
    """Log one model call as an `ai_call` row and return its USD cost.

    Uses the cost OpenRouter reported; falls back to the configured per-token prices.
    """
    cost = usage.cost_usd
    if cost is None:
        cost = (
            usage.input_tokens * settings.compass_ai_input_usd_per_mtok
            + usage.output_tokens * settings.compass_ai_output_usd_per_mtok
        ) / 1_000_000
    log_usage(
        settings.compass_db_path,
        "ai_call",
        name,
        detail=detail,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=cost,
    )
    return cost
