"""Tests for DuckDB-backed observability storage."""

import re
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
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


class TestConstruction:
    """Constructor/schema behavior."""

    def test_init_creates_nested_parent_directory(self, tmp_path: Path) -> None:
        """Asking for a DB in a nested, missing directory creates it."""
        db = ObservabilityDB(tmp_path / "nested" / "dir" / "obs.duckdb")
        assert (tmp_path / "nested" / "dir").is_dir()
        assert db.db_path == tmp_path / "nested" / "dir" / "obs.duckdb"

    def test_init_schema_uses_expected_sql(self, tmp_path: Path) -> None:
        """The schema init issues the exact CREATE/ALTER statements."""
        with patch("obsidian_ai_tools.observability.duckdb.connect") as mock_connect:
            conn = mock_connect.return_value.__enter__.return_value
            ObservabilityDB(tmp_path / "obs.duckdb")

        sqls = [call.args[0] for call in conn.execute.call_args_list]
        assert "ALTER TABLE costs ADD COLUMN IF NOT EXISTS source_type VARCHAR" in sqls
        assert any(sql.strip().startswith("CREATE TABLE IF NOT EXISTS costs") for sql in sqls)
        assert any(sql.strip().startswith("CREATE TABLE IF NOT EXISTS metrics") for sql in sqls)
        assert any(
            sql.strip().startswith("CREATE TABLE IF NOT EXISTS command_invocations") for sql in sqls
        )
        assert any(
            sql.strip().startswith("CREATE TABLE IF NOT EXISTS provider_attempts") for sql in sqls
        )


class TestRecordCost:
    """record_cost SQL and parameter fidelity."""

    def test_insert_parameters_round_trip(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with patch("obsidian_ai_tools.observability.duckdb.connect") as mock_connect:
            db.record_cost(
                "ingest",
                "gpt-4",
                10,
                20,
                0.0123,
                source_type="web",
                source_url="https://x.com",
            )

        mock_connect.assert_called_once_with(str(db.db_path))
        conn = mock_connect.return_value.__enter__.return_value
        sql, params = conn.execute.call_args.args
        assert sql.strip().startswith("INSERT INTO costs")
        assert isinstance(params[0], datetime)
        assert params[1:] == ["ingest", "gpt-4", "web", 10, 20, 0.0123, "https://x.com"]

    def test_failure_logs_warning_with_context(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with (
            patch(
                "obsidian_ai_tools.observability.duckdb.connect",
                side_effect=RuntimeError("locked"),
            ),
            patch("obsidian_ai_tools.observability.logging.getLogger") as mock_gl,
        ):
            db.record_cost("ingest", "m", 1, 1, 0.01)

        mock_gl.assert_called_once_with("obsidian_ai_tools.observability")
        assert mock_gl.return_value.warning.call_args.args[0].startswith("Failed to record cost: ")


class TestRecordMetric:
    """record_metric SQL and parameter fidelity."""

    def test_insert_parameters_round_trip(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with patch("obsidian_ai_tools.observability.duckdb.connect") as mock_connect:
            db.record_metric("web", "success", 1.5, error_type=None, provider_used="provider1")

        mock_connect.assert_called_once_with(str(db.db_path))
        conn = mock_connect.return_value.__enter__.return_value
        sql, params = conn.execute.call_args.args
        assert sql.strip().startswith("INSERT INTO metrics")
        assert isinstance(params[0], datetime)
        assert params[1:] == ["web", "success", 1.5, None, "provider1"]

    def test_failure_logs_warning_with_context(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with (
            patch(
                "obsidian_ai_tools.observability.duckdb.connect",
                side_effect=RuntimeError("locked"),
            ),
            patch("obsidian_ai_tools.observability.logging.getLogger") as mock_gl,
        ):
            db.record_metric("web", "failure", 1.0, error_type="network")

        mock_gl.assert_called_once_with("obsidian_ai_tools.observability")
        assert mock_gl.return_value.warning.call_args.args[0].startswith(
            "Failed to record metric: "
        )


class TestRecordInvocationDetails:
    """record_invocation SQL and parameter fidelity."""

    def test_insert_parameters_round_trip(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with patch("obsidian_ai_tools.observability.duckdb.connect") as mock_connect:
            db.record_invocation("mycmd", "success", 0.5)

        conn = mock_connect.return_value.__enter__.return_value
        sql, params = conn.execute.call_args.args
        assert sql.strip().startswith("INSERT INTO command_invocations")
        assert isinstance(params[0], datetime)
        assert params[1:] == ["mycmd", "success", 0.5, None]

    def test_failure_logs_warning_with_context(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with (
            patch(
                "obsidian_ai_tools.observability.duckdb.connect",
                side_effect=RuntimeError("locked"),
            ),
            patch("obsidian_ai_tools.observability.logging.getLogger") as mock_gl,
        ):
            db.record_invocation("mycmd", "success", 0.5)

        mock_gl.assert_called_once_with("obsidian_ai_tools.observability")
        assert mock_gl.return_value.warning.call_args.args[0].startswith(
            "Failed to record invocation: "
        )


class TestRecordProviderAttemptDetails:
    """record_provider_attempt SQL and parameter fidelity."""

    def test_insert_parameters_round_trip(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with patch("obsidian_ai_tools.observability.duckdb.connect") as mock_connect:
            db.record_provider_attempt(
                "web", "primary", "failure", 0.25, "RuntimeError", "https://x.com"
            )

        conn = mock_connect.return_value.__enter__.return_value
        sql, params = conn.execute.call_args.args
        assert sql.strip().startswith("INSERT INTO provider_attempts")
        assert isinstance(params[0], datetime)
        assert params[1:] == ["web", "primary", "failure", 0.25, "RuntimeError", "https://x.com"]

    def test_failure_logs_warning_with_context(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with (
            patch(
                "obsidian_ai_tools.observability.duckdb.connect",
                side_effect=RuntimeError("locked"),
            ),
            patch("obsidian_ai_tools.observability.logging.getLogger") as mock_gl,
        ):
            db.record_provider_attempt("web", "primary", "success", 0.1)

        mock_gl.assert_called_once_with("obsidian_ai_tools.observability")
        assert mock_gl.return_value.warning.call_args.args[0].startswith(
            "Failed to record provider attempt: "
        )


class TestSummaryWindows:
    """Default 30-day look-back windows."""

    def test_invocation_summary_default_excludes_old_rows(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with duckdb.connect(str(db.db_path)) as conn:
            conn.execute(
                "INSERT INTO command_invocations "
                "(timestamp, command, outcome, duration_seconds) "
                "VALUES (current_timestamp - INTERVAL '30 days' - INTERVAL '12 hours', "
                "'old-cmd', 'success', 1.0)"
            )

        assert db.get_invocation_summary() == []

    def test_provider_summary_default_excludes_old_rows(self, tmp_path: Path) -> None:
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        with duckdb.connect(str(db.db_path)) as conn:
            conn.execute(
                "INSERT INTO provider_attempts "
                "(timestamp, provider, strategy, outcome, duration_seconds) "
                "VALUES (current_timestamp - INTERVAL '30 days' - INTERVAL '12 hours', "
                "'web', 'primary', 'success', 1.0)"
            )

        assert db.get_provider_summary() == []

    def test_last_used_formatted_as_date(self, tmp_path: Path) -> None:
        """last_used is the ISO date prefix of the max timestamp."""
        db = ObservabilityDB(tmp_path / "obs.duckdb")
        db.record_invocation("ingest", "success", 1.0)

        rows = db.get_invocation_summary(days=30)

        assert len(rows) == 1
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rows[0]["last_used"])


class TestGetDb:
    """get_db singleton construction from settings."""

    def test_creates_singleton_at_vault_observability_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(
            "obsidian_ai_tools.config.get_settings",
            lambda: SimpleNamespace(obsidian_vault_path=vault),
        )

        db = get_db()

        assert isinstance(db, ObservabilityDB)
        assert db.db_path == vault / ".kai" / "observability.duckdb"
        assert get_db() is db
