"""Circuit breaker pattern for quarantining failing services."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

CircuitState = Literal["CLOSED", "OPEN", "HALF_OPEN"]


class CircuitBreakerState(BaseModel):
    """Persistent state for circuit breaker."""

    state: CircuitState = Field(default="CLOSED")
    failure_count: int = Field(default=0)
    last_failure_time: datetime | None = Field(default=None)
    opened_at: datetime | None = Field(default=None)


class CircuitBreaker:
    """Circuit breaker to quarantine failing transcript providers.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Quarantined, requests are blocked
    - HALF_OPEN: Testing if service recovered
    """

    def __init__(
        self,
        state_file: Path,
        failure_threshold: int = 3,
        timeout_hours: int = 2,
    ):
        """Initialize circuit breaker.

        Args:
            state_file: Path to persist state
            failure_threshold: Number of failures before opening circuit
            timeout_hours: Hours to keep circuit open before trying again
        """
        self.state_file = state_file
        self.failure_threshold = failure_threshold
        self.timeout = timedelta(hours=timeout_hours)

        # Ensure parent directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing state or create new
        self.state = self._load_state()

    def _load_state(self) -> CircuitBreakerState:
        """Load state from file or create new."""
        if self.state_file.exists():
            try:
                with self.state_file.open("r") as f:
                    data = json.load(f)
                return CircuitBreakerState(**data)
            except (json.JSONDecodeError, ValueError):
                # Corrupted state file - start fresh
                pass

        return CircuitBreakerState()

    def _save_state(self) -> None:
        """Persist state to file."""
        with self.state_file.open("w") as f:
            json.dump(
                self.state.model_dump(mode="json"),
                f,
                indent=2,
                default=str,
            )

    def is_open(self) -> bool:
        """Check if circuit is open (service quarantined).

        Returns:
            True if circuit is open and requests should be blocked
        """
        if self.state.state == "CLOSED":
            return False

        if self.state.state == "OPEN":
            # Check if timeout has elapsed
            if self.state.opened_at:
                elapsed = datetime.now() - self.state.opened_at
                if elapsed >= self.timeout:
                    # Move to half-open to test service
                    self.state.state = "HALF_OPEN"
                    self._save_state()
                    return False

            return True

        # HALF_OPEN state - allow one request through to test
        return False

    def record_success(self) -> None:
        """Record successful request.

        If in HALF_OPEN state, close the circuit.
        """
        if self.state.state == "HALF_OPEN":
            # Service recovered - close circuit
            self.state.state = "CLOSED"

        # Reset failure count on any success
        self.state.failure_count = 0
        self.state.last_failure_time = None
        self.state.opened_at = None
        self._save_state()

    def record_failure(self) -> None:
        """Record failed request.

        Opens circuit if failure threshold is reached.
        """
        self.state.failure_count += 1
        self.state.last_failure_time = datetime.now()

        if self.state.state == "HALF_OPEN":
            # Failed during test - reopen circuit
            self.state.state = "OPEN"
            self.state.opened_at = datetime.now()

        elif self.state.failure_count >= self.failure_threshold:
            # Threshold reached - open circuit
            self.state.state = "OPEN"
            self.state.opened_at = datetime.now()

        self._save_state()

    def reset(self) -> None:
        """Manually reset circuit breaker to closed state."""
        self.state = CircuitBreakerState()
        self._save_state()

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics.

        Returns:
            Dictionary with current state and stats
        """
        stats: dict[str, Any] = {
            "state": self.state.state,
            "failure_count": self.state.failure_count,
            "failure_threshold": self.failure_threshold,
            "timeout_hours": self.timeout.total_seconds() / 3600,
        }

        if self.state.last_failure_time:
            stats["last_failure"] = self.state.last_failure_time.isoformat()

        if self.state.opened_at:
            stats["opened_at"] = self.state.opened_at.isoformat()
            elapsed = datetime.now() - self.state.opened_at
            stats["time_remaining_hours"] = max(0, (self.timeout - elapsed).total_seconds() / 3600)

        return stats
