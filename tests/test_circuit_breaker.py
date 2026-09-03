"""Tests for circuit breaker functionality."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import obsidian_ai_tools.circuit_breaker as circuit_breaker_module
from obsidian_ai_tools.circuit_breaker import CircuitBreaker


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    """Create a temporary state file path."""
    return tmp_path / "circuit_breaker_state.json"


@pytest.fixture
def breaker(state_file: Path) -> CircuitBreaker:
    """Create a CircuitBreaker instance."""
    return CircuitBreaker(state_file, failure_threshold=3, timeout_hours=1)


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_initial_state_closed(self, breaker: CircuitBreaker) -> None:
        """Test circuit starts in CLOSED state."""
        assert not breaker.is_open()
        assert breaker.state.state == "CLOSED"
        assert breaker.state.failure_count == 0

    def test_record_success_resets_failures(self, breaker: CircuitBreaker) -> None:
        """Test successful request resets failure count."""
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state.failure_count == 2

        breaker.record_success()
        assert breaker.state.failure_count == 0
        assert not breaker.is_open()

    def test_opens_after_threshold(self, breaker: CircuitBreaker) -> None:
        """Test circuit opens after failure threshold."""
        assert not breaker.is_open()

        breaker.record_failure()
        assert not breaker.is_open()

        breaker.record_failure()
        assert not breaker.is_open()

        breaker.record_failure()
        # Should now be open
        assert breaker.is_open()
        assert breaker.state.state == "OPEN"

    def test_stays_open_during_timeout(self, state_file: Path) -> None:
        """Test circuit stays open during timeout period."""
        breaker = CircuitBreaker(state_file, failure_threshold=2, timeout_hours=24)

        breaker.record_failure()
        breaker.record_failure()

        assert breaker.is_open()
        # Should still be open after checking again
        assert breaker.is_open()

    def test_moves_to_half_open_after_timeout(self, state_file: Path) -> None:
        """Test circuit moves to HALF_OPEN after timeout."""
        # Create breaker with 1 hour timeout
        breaker = CircuitBreaker(state_file, failure_threshold=2, timeout_hours=1)

        breaker.record_failure()
        breaker.record_failure()

        assert breaker.is_open()
        assert breaker.state.state == "OPEN"

        # Manually set opened_at to past (2 hours ago)
        breaker.state.opened_at = datetime.now() - timedelta(hours=2)
        breaker._save_state()

        # Should move to HALF_OPEN
        assert not breaker.is_open()
        assert breaker.state.state == "HALF_OPEN"

    def test_half_open_success_closes_circuit(self, state_file: Path) -> None:
        """Test successful request in HALF_OPEN state closes circuit."""
        breaker = CircuitBreaker(state_file, failure_threshold=2, timeout_hours=1)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()

        # Move to HALF_OPEN (set time to 2 hours ago)
        breaker.state.opened_at = datetime.now() - timedelta(hours=2)
        breaker._save_state()
        breaker.is_open()  # Triggers state transition

        assert breaker.state.state == "HALF_OPEN"

        # Success should close
        breaker.record_success()
        assert breaker.state.state == "CLOSED"
        assert not breaker.is_open()

    def test_half_open_failure_reopens_circuit(self, state_file: Path) -> None:
        """Test failed request in HALF_OPEN state reopens circuit."""
        breaker = CircuitBreaker(state_file, failure_threshold=2, timeout_hours=1)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()

        # Move to HALF_OPEN (set time to 2 hours ago)
        breaker.state.opened_at = datetime.now() - timedelta(hours=2)
        breaker._save_state()
        breaker.is_open()  # Triggers state transition

        assert breaker.state.state == "HALF_OPEN"

        # Failure should reopen
        breaker.record_failure()
        assert breaker.state.state == "OPEN"
        assert breaker.is_open()

    def test_state_persistence(self, state_file: Path) -> None:
        """Test state is persisted across instances."""
        # Create breaker and record failures
        breaker1 = CircuitBreaker(state_file, failure_threshold=3)
        breaker1.record_failure()
        breaker1.record_failure()
        breaker1.record_failure()
        assert breaker1.is_open()

        # Create new instance - should load persisted state
        breaker2 = CircuitBreaker(state_file, failure_threshold=3)
        assert breaker2.is_open()
        assert breaker2.state.failure_count == 3
        assert breaker2.state.state == "OPEN"

    def test_manual_reset(self, breaker: CircuitBreaker) -> None:
        """Test manual reset of circuit breaker."""
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open()

        breaker.reset()
        assert not breaker.is_open()
        assert breaker.state.state == "CLOSED"
        assert breaker.state.failure_count == 0

    def test_get_stats(self, breaker: CircuitBreaker) -> None:
        """Test circuit breaker statistics."""
        stats = breaker.get_stats()
        assert stats["state"] == "CLOSED"
        assert stats["failure_count"] == 0
        assert stats["failure_threshold"] == 3
        assert stats["timeout_hours"] == 1

        breaker.record_failure()
        stats = breaker.get_stats()
        assert stats["failure_count"] == 1
        assert "last_failure" in stats

        breaker.record_failure()
        breaker.record_failure()
        stats = breaker.get_stats()
        assert stats["state"] == "OPEN"
        assert "opened_at" in stats
        assert "time_remaining_hours" in stats

    def test_corrupted_state_file(self, state_file: Path) -> None:
        """Test handling of corrupted state file."""
        # Create corrupted state file
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("invalid json{{{")

        # Should start with fresh state
        breaker = CircuitBreaker(state_file)
        assert breaker.state.state == "CLOSED"
        assert breaker.state.failure_count == 0
        assert not breaker.is_open()

    def test_different_thresholds(self, state_file: Path) -> None:
        """Test circuit breaker with different thresholds."""
        # Test with threshold of 1
        breaker = CircuitBreaker(state_file, failure_threshold=1)
        breaker.record_failure()
        assert breaker.is_open()

        # Test with threshold of 5
        breaker2 = CircuitBreaker(state_file, failure_threshold=5)
        breaker2.reset()

        for _i in range(4):
            breaker2.record_failure()
            assert not breaker2.is_open()

        breaker2.record_failure()
        assert breaker2.is_open()

    def test_default_configuration(self, state_file: Path) -> None:
        """Test the documented default threshold and timeout."""
        breaker = CircuitBreaker(state_file)
        stats = breaker.get_stats()
        assert stats["failure_threshold"] == 3
        assert stats["timeout_hours"] == 2

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        """The state file's parent directories are created on construction."""
        state_file = tmp_path / "nested" / "dir" / "state.json"
        breaker = CircuitBreaker(state_file, failure_threshold=1)
        breaker.record_failure()
        assert state_file.exists()

    def test_persisted_state_file_format(self, breaker: CircuitBreaker) -> None:
        """State is persisted as pretty-printed JSON with ISO-8601 datetimes."""
        breaker.record_failure()
        raw = breaker.state_file.read_text()
        data = json.loads(raw)
        assert data["failure_count"] == 1
        # indent=2 pretty printing
        assert '\n  "failure_count": 1,' in raw
        # json-mode serialization renders datetimes with the ISO "T" separator
        assert "T" in data["last_failure_time"]

    def test_success_clears_timestamps(self, state_file: Path) -> None:
        """record_success resets every failure-related field."""
        breaker = CircuitBreaker(state_file, failure_threshold=1, timeout_hours=1)
        breaker.record_failure()
        assert breaker.state.last_failure_time is not None
        assert breaker.state.opened_at is not None

        breaker.record_success()
        assert breaker.state.last_failure_time is None
        assert breaker.state.opened_at is None

    def test_half_open_reopen_keeps_opened_at(self, state_file: Path) -> None:
        """A failure while HALF_OPEN reopens with a fresh opened_at."""
        breaker = CircuitBreaker(state_file, failure_threshold=2, timeout_hours=1)
        breaker.record_failure()
        breaker.record_failure()
        breaker.state.opened_at = datetime.now() - timedelta(hours=2)
        breaker.is_open()  # HALF_OPEN now

        breaker.record_failure()
        assert breaker.state.state == "OPEN"
        assert breaker.state.opened_at is not None

    def test_timeout_boundary_transitions_to_half_open(
        self, state_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The timeout is inclusive: elapsed == timeout already half-opens."""
        frozen = datetime(2024, 1, 1, 12, 0, 0)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003
                return frozen

        monkeypatch.setattr(circuit_breaker_module, "datetime", FrozenDateTime)
        breaker = CircuitBreaker(state_file, failure_threshold=1, timeout_hours=1)
        breaker.record_failure()
        breaker.state.opened_at = frozen - breaker.timeout  # exactly at the boundary

        assert not breaker.is_open()
        assert breaker.state.state == "HALF_OPEN"

    def test_get_stats_reports_time_remaining(self, state_file: Path) -> None:
        """get_stats exposes ISO timestamps and the remaining timeout in hours."""
        breaker = CircuitBreaker(state_file, failure_threshold=1, timeout_hours=1)
        breaker.record_failure()
        breaker.state.opened_at = datetime.now() - timedelta(minutes=30)

        stats = breaker.get_stats()
        # Timestamps are ISO-8601 strings, not raw datetimes
        datetime.fromisoformat(stats["last_failure"])
        datetime.fromisoformat(stats["opened_at"])
        assert stats["time_remaining_hours"] == pytest.approx(0.5, abs=1e-4)

        # The remaining time is clamped at zero once the timeout has elapsed
        breaker.state.opened_at = datetime.now() - timedelta(hours=2)
        assert breaker.get_stats()["time_remaining_hours"] == 0
