"""Observability database management using DuckDB."""

import functools
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import duckdb
import typer


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

            # Command invocations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS command_invocations (
                    timestamp        TIMESTAMP NOT NULL,
                    command          VARCHAR   NOT NULL,
                    outcome          VARCHAR   NOT NULL,
                    duration_seconds DECIMAL(8,3),
                    error_type       VARCHAR
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cmd_timestamp
                ON command_invocations(timestamp DESC)
            """)

            # Provider attempts table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS provider_attempts (
                    timestamp        TIMESTAMP NOT NULL,
                    provider         VARCHAR   NOT NULL,
                    strategy         VARCHAR   NOT NULL,
                    outcome          VARCHAR   NOT NULL,
                    duration_seconds DECIMAL(8,3),
                    error_type       VARCHAR,
                    url              VARCHAR
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_prov_timestamp
                ON provider_attempts(timestamp DESC)
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

    def record_invocation(
        self,
        command: str,
        outcome: str,
        duration_seconds: float,
        error_type: str | None = None,
    ) -> None:
        """Record a CLI command invocation.

        Args:
            command: Command name (e.g. "ingest", "process-inbox")
            outcome: "success", "error", or "user_abort"
            duration_seconds: Wall-clock duration
            error_type: Exception class name when outcome is "error"
        """
        try:
            with duckdb.connect(str(self.db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO command_invocations
                        (timestamp, command, outcome, duration_seconds, error_type)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [datetime.now(), command, outcome, duration_seconds, error_type],
                )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to record invocation: {e}")

    def get_invocation_summary(self, days: int = 30) -> list[dict[str, Any]]:
        """Return per-command call counts, success rate, and last-used timestamp.

        Args:
            days: Look-back window in days

        Returns:
            List of dicts sorted by call count descending.
        """
        with duckdb.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT
                    command,
                    COUNT(*) AS calls,
                    ROUND(
                        SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) * 100.0
                        / COUNT(*), 1
                    ) AS success_pct,
                    MAX(timestamp) AS last_used
                FROM command_invocations
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                GROUP BY command
                ORDER BY calls DESC
                """,
                [days],
            ).fetchall()
            return [
                {
                    "command": row[0],
                    "calls": int(row[1]),
                    "success_pct": float(row[2]),
                    "last_used": str(row[3])[:10],
                }
                for row in rows
            ]

    def record_provider_attempt(
        self,
        provider: str,
        strategy: str,
        outcome: str,
        duration_seconds: float,
        error_type: str | None = None,
        url: str | None = None,
    ) -> None:
        """Record a single provider attempt (primary or fallback).

        Args:
            provider: Provider name ("web", "pdf", "youtube", "file")
            strategy: "primary" or "fallback"
            outcome: "success" or "failure"
            duration_seconds: Wall-clock duration of this attempt
            error_type: Exception class name on failure
            url: Source URL
        """
        try:
            with duckdb.connect(str(self.db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO provider_attempts
                        (timestamp, provider, strategy, outcome,
                         duration_seconds, error_type, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        datetime.now(),
                        provider,
                        strategy,
                        outcome,
                        duration_seconds,
                        error_type,
                        url,
                    ],
                )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to record provider attempt: {e}")

    def get_provider_summary(self, days: int = 30) -> list[dict[str, Any]]:
        """Return per-provider/strategy attempt counts and success rate.

        Args:
            days: Look-back window in days

        Returns:
            List of dicts sorted by provider then strategy.
        """
        with duckdb.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT
                    provider,
                    strategy,
                    COUNT(*) AS attempts,
                    ROUND(
                        SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) * 100.0
                        / COUNT(*), 1
                    ) AS success_pct
                FROM provider_attempts
                WHERE timestamp > current_timestamp - (? * INTERVAL '1 DAY')
                GROUP BY provider, strategy
                ORDER BY provider, strategy
                """,
                [days],
            ).fetchall()
            return [
                {
                    "provider": row[0],
                    "strategy": row[1],
                    "attempts": int(row[2]),
                    "success_pct": float(row[3]),
                }
                for row in rows
            ]


_db: Optional["ObservabilityDB"] = None


def get_db(vault_path: Path | None = None) -> "ObservabilityDB":
    """Return the shared ObservabilityDB singleton, creating it on first call.

    Args:
        vault_path: Override the vault root used to derive the database path.
            When omitted, falls back to ``settings.obsidian_vault_path``.
    """
    global _db
    if _db is None:
        if vault_path is None:
            from .config import get_settings

            settings = get_settings()
            vault_path = settings.obsidian_vault_path
        from ._vault_store import VaultStore

        store = VaultStore(vault_path)
        _db = ObservabilityDB(store.vault_path / ".kai" / "observability.duckdb")
    return _db


def _set_db_for_test(db: Optional["ObservabilityDB"]) -> None:
    """Inject or reset the singleton. Test use only."""
    global _db
    _db = db


def track_command(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that records command invocation outcome and duration to ObservabilityDB.

    Apply inside @app.command() so Typer sees the original signature.
    Observability failures are swallowed and never surface to the user.

    Args:
        name: Command name as it should appear in the usage report.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            outcome: str = "success"
            error_type: str | None = None
            try:
                return func(*args, **kwargs)
            except typer.Exit as exc:
                if exc.exit_code != 0:
                    outcome, error_type = "error", "Exit"
                raise
            except typer.Abort:
                outcome = "user_abort"
                raise
            except Exception as exc:
                outcome, error_type = "error", type(exc).__name__
                raise
            finally:
                try:
                    get_db().record_invocation(name, outcome, time.monotonic() - start, error_type)
                except Exception:  # nosec B110
                    pass  # never block the command

        return wrapper

    return decorator
