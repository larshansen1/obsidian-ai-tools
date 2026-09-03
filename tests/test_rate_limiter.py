"""Unit tests for the per-domain rate limiter in utils/rate_limiter.py.

The limiter is pure-clock logic, so every test fakes time.time (and time.sleep
via the standard patch helper) instead of actually waiting.
"""

from unittest.mock import patch

from obsidian_ai_tools.utils.rate_limiter import RateLimiter


class _Clock:
    """Fake monotonic clock: time.time() returns ``now`` until it is advanced."""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_init_defaults_delay_to_2_seconds() -> None:
    limiter = RateLimiter()
    assert limiter.delay == 2.0
    assert limiter.last_access == {}


def test_init_accepts_custom_delay() -> None:
    limiter = RateLimiter(delay=5.0)
    assert limiter.delay == 5.0


def test_wait_ignores_urls_without_a_domain() -> None:
    """A relative URL has an empty netloc, so no domain is tracked or slept on."""
    limiter = RateLimiter()
    with patch("time.sleep") as mock_sleep:
        limiter.wait("relative/path.html")
    mock_sleep.assert_not_called()
    assert limiter.last_access == {}


def test_wait_first_request_stamps_the_domain_without_sleeping() -> None:
    limiter = RateLimiter()
    clock = _Clock(1000.0)
    with (
        patch("time.time", side_effect=clock),
        patch("time.sleep") as mock_sleep,
    ):
        limiter.wait("https://example.com/article")
    mock_sleep.assert_not_called()
    assert limiter.last_access == {"example.com": 1000.0}


def test_wait_first_request_sleeps_for_the_full_gap() -> None:
    """The initial gap is measured from an 0.0 baseline, not 1.0 or later."""
    limiter = RateLimiter()
    clock = _Clock(0.5)
    with (
        patch("time.time", side_effect=clock),
        patch("time.sleep") as mock_sleep,
    ):
        limiter.wait("https://example.com/a")
    mock_sleep.assert_called_once_with(1.5)
    assert limiter.last_access["example.com"] == 0.5


def test_wait_sleeps_to_enforce_the_gap_between_requests() -> None:
    """A second request 1.5s later must sleep the remaining 0.5s of the 2s gap."""
    limiter = RateLimiter()
    clock = _Clock(1000.0)
    with (
        patch("time.time", side_effect=clock),
        patch("time.sleep") as mock_sleep,
    ):
        limiter.wait("https://example.com/a")
        clock.now = 1001.5
        limiter.wait("https://example.com/b")  # same domain -> same bucket
    mock_sleep.assert_called_once_with(0.5)
    assert limiter.last_access["example.com"] == 1001.5


def test_wait_skips_sleep_when_the_gap_equals_the_delay() -> None:
    """Exactly 2.0s elapsed: the guard is strict `elapsed < delay`, so no sleep."""
    limiter = RateLimiter()
    clock = _Clock(1000.0)
    with (
        patch("time.time", side_effect=clock),
        patch("time.sleep") as mock_sleep,
    ):
        limiter.wait("https://example.com/a")
        clock.now = 1002.0
        limiter.wait("https://example.com/a")
    mock_sleep.assert_not_called()
    assert limiter.last_access["example.com"] == 1002.0
