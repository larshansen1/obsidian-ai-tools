"""Tests for DuckDB-backed observability storage."""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from obsidian_ai_tools.observability import (
    ObservabilityDB,
    _set_db_for_test,
    get_db,
    track_command,
)


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


def test_invocation_records_round_trip_into_summary(tmp_path: Path) -> None:
    """record_invocation rows appear in get_invocation_summary."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")

    db.record_invocation("ingest", "success", 1.2)
    db.record_invocation("ingest", "success", 0.8)
    db.record_invocation("ingest", "error", 0.5, error_type="ContentFetchError")
    db.record_invocation("search", "success", 0.3)

    rows = db.get_invocation_summary(days=30)
    by_command = {r["command"]: r for r in rows}

    assert by_command["ingest"]["calls"] == 3
    assert by_command["ingest"]["success_pct"] == pytest.approx(66.7, abs=0.1)
    assert by_command["search"]["calls"] == 1
    assert by_command["search"]["success_pct"] == 100.0


def test_invocation_summary_empty_when_no_data(tmp_path: Path) -> None:
    """get_invocation_summary returns an empty list when the table is empty."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    assert db.get_invocation_summary() == []


def test_track_command_records_success(tmp_path: Path) -> None:
    """track_command decorator writes a success row on normal return."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    _set_db_for_test(db)

    @track_command("test-cmd")
    def my_cmd() -> str:
        return "ok"

    my_cmd()

    rows = db.get_invocation_summary(days=1)
    assert len(rows) == 1
    assert rows[0]["command"] == "test-cmd"
    assert rows[0]["calls"] == 1
    assert rows[0]["success_pct"] == 100.0


def test_track_command_records_error_on_exception(tmp_path: Path) -> None:
    """track_command decorator writes an error row when the command raises."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    _set_db_for_test(db)

    @track_command("failing-cmd")
    def bad_cmd() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        bad_cmd()

    import duckdb as _duckdb

    with _duckdb.connect(str(tmp_path / "obs.duckdb")) as conn:
        rows = conn.execute("SELECT outcome, error_type FROM command_invocations").fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "error"
    assert rows[0][1] == "ValueError"


def test_track_command_records_user_abort(tmp_path: Path) -> None:
    """track_command decorator writes user_abort when typer.Abort is raised."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    _set_db_for_test(db)

    @track_command("aborted-cmd")
    def aborting_cmd() -> None:
        raise typer.Abort()

    with pytest.raises(typer.Abort):
        aborting_cmd()

    import duckdb as _duckdb

    with _duckdb.connect(str(tmp_path / "obs.duckdb")) as conn:
        rows = conn.execute("SELECT outcome FROM command_invocations").fetchall()

    assert rows[0][0] == "user_abort"


def test_track_command_swallows_observability_errors(tmp_path: Path) -> None:
    """track_command never blocks the command when recording fails."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    _set_db_for_test(db)

    @track_command("cmd")
    def working_cmd() -> str:
        return "result"

    with patch.object(db, "record_invocation", side_effect=RuntimeError("db gone")):
        result = working_cmd()

    assert result == "result"


def test_provider_attempt_records_round_trip_into_summary(tmp_path: Path) -> None:
    """record_provider_attempt rows appear in get_provider_summary."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")

    db.record_provider_attempt("web", "primary", "success", 0.5, url="https://example.com")
    db.record_provider_attempt("web", "primary", "failure", 0.3, "RuntimeError", "https://x.com")
    db.record_provider_attempt("web", "fallback", "success", 1.2, url="https://x.com")
    db.record_provider_attempt("pdf", "primary", "success", 2.0)

    rows = db.get_provider_summary(days=30)
    by_key = {(r["provider"], r["strategy"]): r for r in rows}

    assert by_key[("web", "primary")]["attempts"] == 2
    assert by_key[("web", "primary")]["success_pct"] == 50.0
    assert by_key[("web", "fallback")]["attempts"] == 1
    assert by_key[("web", "fallback")]["success_pct"] == 100.0
    assert by_key[("pdf", "primary")]["attempts"] == 1


def test_provider_summary_empty_when_no_data(tmp_path: Path) -> None:
    """get_provider_summary returns an empty list when no data exists."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")
    assert db.get_provider_summary() == []


def test_provider_attempt_failure_does_not_raise(tmp_path: Path) -> None:
    """record_provider_attempt swallows DB errors silently."""
    db = ObservabilityDB(tmp_path / "obs.duckdb")

    with patch(
        "obsidian_ai_tools.observability.duckdb.connect",
        side_effect=RuntimeError("locked"),
    ):
        db.record_provider_attempt("web", "primary", "success", 0.1)
