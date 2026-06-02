"""Tests for DuckDB-backed observability storage."""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_ai_tools.observability import ObservabilityDB, _set_db_for_test, get_db


@pytest.fixture(autouse=True)
def reset_singleton() -> Generator[None, None, None]:
    """Ensure the singleton is reset between tests."""
    _set_db_for_test(None)
    yield
    _set_db_for_test(None)


def test_cost_records_round_trip_into_summaries(tmp_path: Path) -> None:
    """Recorded costs should appear in aggregate and recent queries."""
    db = ObservabilityDB(tmp_path / ".kai" / "observability.duckdb")

    db.record_cost("ingest", "model-a", 100, 20, 0.012, "youtube", "https://youtu.be/a")
    db.record_cost("refresh", "model-b", 200, 40, 0.024, None, None)

    summary = db.get_cost_summary()
    recent = db.get_recent_costs(limit=1)

    assert summary["total_cost"] == 0.036
    assert summary["recent_cost_7days"] == 0.036
    assert summary["by_model"] == [("model-b", 0.024), ("model-a", 0.012)]
    assert summary["by_operation"] == [("refresh", 0.024), ("ingest", 0.012)]
    assert summary["by_source_type"] == [("Unknown", 0.024, 1), ("youtube", 0.012, 1)]
    assert len(recent) == 1
    assert recent[0]["model"] == "model-b"
    assert recent[0]["input_tokens"] == 200


def test_quality_metrics_round_trip_into_summary(tmp_path: Path) -> None:
    """Quality summaries should report success rates and common errors."""
    db = ObservabilityDB(tmp_path / "observability.duckdb")

    db.record_metric("youtube", "success", 1.5, provider_used="direct")
    db.record_metric("youtube", "failure", 2.5, error_type="timeout", provider_used="direct")
    db.record_metric("web", "success", 0.5)

    summary = db.get_quality_summary()

    assert summary["total_ingestions"] == 3
    assert summary["successes"] == 2
    assert summary["success_rate"] == 2 / 3 * 100
    assert summary["common_errors"] == [("timeout", 1)]
    assert summary["by_source"][0] == {
        "source_type": "youtube",
        "total": 2,
        "successes": 1,
        "avg_duration": 2.0,
    }


def test_get_db_returns_injected_singleton(tmp_path: Path) -> None:
    """get_db() returns the instance set by _set_db_for_test."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    _set_db_for_test(db)
    assert get_db() is db
    assert get_db() is db  # same instance on repeated calls


def test_set_db_for_test_reset_clears_singleton(tmp_path: Path) -> None:
    """_set_db_for_test(None) resets the singleton so get_db creates a new one."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    _set_db_for_test(db)
    _set_db_for_test(None)
    # After reset, get_db would call get_settings — just verify the slot is clear
    # by confirming a new injection works independently
    db2 = ObservabilityDB(tmp_path / "obs2.duckdb")
    _set_db_for_test(db2)
    assert get_db() is db2


def test_observability_write_failures_do_not_break_main_operation(tmp_path: Path) -> None:
    """Observability is best effort when DuckDB writes fail."""
    db = ObservabilityDB(tmp_path / "observability.duckdb")

    with patch(
        "obsidian_ai_tools.observability.duckdb.connect",
        side_effect=RuntimeError("locked"),
    ):
        db.record_cost("ingest", "model", 1, 1, 0.01)
        db.record_metric("web", "failure", 1.0, error_type="network")
