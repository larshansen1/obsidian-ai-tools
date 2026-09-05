"""Providers package - content ingestion backends."""

import logging

from ..observability import get_db
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Shared rate limiter - one instance for all HTTP providers so concurrent
# web+pdf requests don't race past each other's delay.
_limiter = RateLimiter(delay=2.0)


def _record_attempt(
    provider: str,
    strategy: str,
    outcome: str,
    duration: float,
    error_type: str | None = None,
    url: str | None = None,
) -> None:
    """Record a provider attempt to the observability DB, swallowing failures."""
    try:
        get_db().record_provider_attempt(provider, strategy, outcome, duration, error_type, url)
    except Exception:  # nosec B110
        pass
