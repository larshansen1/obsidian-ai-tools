"""Observability database management using DuckDB."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb


class ObservabilityDB:
    """Manages observability data storage in DuckDB."""

    def __init__(self, db_path: Path):
        """Initialize database connection.

        Args:
            db_path: Path to DuckDB database file
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema if not exists."""
        with duckdb.connect(str(self.db_path)) as conn:
            # Costs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS costs (
                    timestamp TIMESTAMP NOT NULL,
                    operation VARCHAR NOT NULL,
                    model VARCHAR NOT NULL,
                    source_type VARCHAR,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_cost_usd DECIMAL(10,6) NOT NULL,
                    source_url VARCHAR
                )
            """)

            # Migration for databases created before source_type was tracked.
            conn.execute("ALTER TABLE costs ADD COLUMN IF NOT EXISTS source_type VARCHAR")

            # Metrics table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    timestamp TIMESTAMP NOT NULL,
                    source_type VARCHAR NOT NULL,
                    outcome VARCHAR NOT NULL,
                    duration_seconds DECIMAL(8,3) NOT NULL,
                    error_type VARCHAR,
                    provider_used VARCHAR
                )
            """)

            # Create indexes for common queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_costs_timestamp
                ON costs(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_timestamp
                ON metrics(timestamp DESC)
            """)

    def record_cost(
        self,
        operation: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_cost_usd: float,
        source_type: str | None = None,
        source_url: str | None = None,
    ) -> None:
        """Record an LLM API cost.

        Args:
            operation: Operation type (e.g., "ingest", "generate_note")
            model: Model identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            total_cost_usd: Total cost in USD from API response
            source_type: Type of source (youtube, web, pdf, file)
            source_url: Optional source URL
        """
        try:
            with duckdb.connect(str(self.db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO costs (
                        timestamp, operation, model, source_type,
                        input_tokens, output_tokens,
                        total_cost_usd, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        datetime.now(),
                        operation,
                        model,
                        source_type,
                        input_tokens,
                        output_tokens,
                        total_cost_usd,
                        source_url,
                    ],
                )
        except Exception as e:
            # Never fail the main operation due to observability
            import logging

            logging.getLogger(__name__).warning(f"Failed to record cost: {e}")

    def record_metric(
        self,
        source_type: str,
        outcome: str,
        duration_seconds: float,
        error_type: str | None = None,
        provider_used: str | None = None,
    ) -> None:
        """Record an ingestion metric.

        Args:
            source_type: Source type (e.g., "youtube", "web", "pdf")
            outcome: Outcome ("success" or "failure")
            duration_seconds: Duration in seconds
            error_type: Error type if failed
            provider_used: Provider used (for YouTube)
        """
        try:
            with duckdb.connect(str(self.db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO metrics (
                        timestamp, source_type, outcome,
                        duration_seconds, error_type, provider_used
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        datetime.now(),
                        source_type,
                        outcome,
                        duration_seconds,
                        error_type,
                        provider_used,
                    ],
                )
        except Exception as e:
            # Never fail the main operation due to observability
            import logging

            logging.getLogger(__name__).warning(f"Failed to record metric: {e}")

    def get_cost_summary(self, days: int = 30) -> dict:
        """Get cost summary for the last N days.

        Args:
            days: Number of days to include

        Returns:
            Dictionary with cost statistics
        """
        with duckdb.connect(str(self.db_path)) as conn:
            # Total cost
            result = conn.execute(
                """
                SELECT COALESCE(SUM(total_cost_usd), 0) as total_cost
                FROM costs
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                """,
                [days],
            ).fetchone()
            total_cost = float(result[0]) if result else 0.0

            # Cost by model
            by_model = conn.execute(
                """
                SELECT model, SUM(total_cost_usd) as cost
                FROM costs
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                GROUP BY model
                ORDER BY cost DESC
                """,
                [days],
            ).fetchall()

            # Cost by source type
            by_source_type = conn.execute(
                """
                SELECT
                    COALESCE(source_type, 'Unknown') as source_type,
                    SUM(total_cost_usd) as cost,
                    COUNT(*) as count
                FROM costs
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                GROUP BY source_type
                ORDER BY cost DESC
                """,
                [days],
            ).fetchall()

            # Cost by operation
            by_operation = conn.execute(
                """
                SELECT operation, SUM(total_cost_usd) as cost
                FROM costs
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                GROUP BY operation
                ORDER BY cost DESC
                """,
                [days],
            ).fetchall()

            # Recent cost (last 7 days)
            recent = conn.execute(
                """
                SELECT COALESCE(SUM(total_cost_usd), 0) as cost
                FROM costs
                WHERE timestamp > current_timestamp - INTERVAL '7' DAYS
                """
            ).fetchone()
            recent_cost = float(recent[0]) if recent else 0.0

            return {
                "total_cost": total_cost,
                "by_model": [(model, float(cost)) for model, cost in by_model],
                "by_source_type": [
                    (st, float(cost), int(count)) for st, cost, count in by_source_type
                ],
                "by_operation": [(op, float(cost)) for op, cost in by_operation],
                "recent_cost_7days": recent_cost,
            }

    def get_recent_costs(self, limit: int = 10) -> list[dict]:
        """Get recent individual cost records.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of cost records
        """
        with duckdb.connect(str(self.db_path)) as conn:
            results = conn.execute(
                """
                SELECT
                    timestamp, source_type, model,
                    input_tokens, output_tokens, total_cost_usd, source_url
                FROM costs
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()

            return [
                {
                    "timestamp": str(ts),
                    "source_type": st,
                    "model": model,
                    "input_tokens": int(inp),
                    "output_tokens": int(out),
                    "total_cost_usd": float(cost),
                    "source_url": url,
                }
                for ts, st, model, inp, out, cost, url in results
            ]

    def get_quality_summary(self, days: int = 30) -> dict:
        """Get quality metrics for the last N days.

        Args:
            days: Number of days to include

        Returns:
            Dictionary with quality statistics
        """
        with duckdb.connect(str(self.db_path)) as conn:
            # Total ingestions
            result = conn.execute(
                """
                SELECT COUNT(*) as total,
                       COALESCE(
                           SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END), 0
                       ) as successes
                FROM metrics
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                """,
                [days],
            ).fetchone()

            total = int(result[0]) if result else 0
            successes = int(result[1]) if result and result[1] is not None else 0
            success_rate = (successes / total * 100) if total > 0 else 0.0

            # By source type
            by_source = conn.execute(
                """
                SELECT
                    source_type,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successes,
                    AVG(duration_seconds) as avg_duration
                FROM metrics
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                GROUP BY source_type
                ORDER BY total DESC
                """,
                [days],
            ).fetchall()

            # Common errors
            errors = conn.execute(
                """
                SELECT error_type, COUNT(*) as count
                FROM metrics
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                  AND outcome = 'failure'
                  AND error_type IS NOT NULL
                GROUP BY error_type
                ORDER BY count DESC
                LIMIT 5
                """,
                [days],
            ).fetchall()

            return {
                "total_ingestions": total,
                "successes": successes,
                "success_rate": success_rate,
                "by_source": [
                    {
                        "source_type": st,
                        "total": int(t),
                        "successes": int(s),
                        "avg_duration": float(d),
                    }
                    for st, t, s, d in by_source
                ],
                "common_errors": [(error, int(count)) for error, count in errors],
            }


_db: Optional["ObservabilityDB"] = None


def get_db() -> "ObservabilityDB":
    """Return the shared ObservabilityDB singleton, creating it on first call."""
    global _db
    if _db is None:
        from .config import get_settings

        settings = get_settings()
        _db = ObservabilityDB(settings.obsidian_vault_path / ".kai" / "observability.duckdb")
    return _db


def _set_db_for_test(db: Optional["ObservabilityDB"]) -> None:
    """Inject or reset the singleton. Test use only."""
    global _db
    _db = db
