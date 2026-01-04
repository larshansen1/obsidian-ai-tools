"""Rate limiting utility for web scraping."""

import time
from urllib.parse import urlparse


class RateLimiter:
    """Simple rate limiter tracking access limits per domain."""

    def __init__(self, delay: float = 2.0) -> None:
        """Initialize rate limiter.

        Args:
            delay: Minimum seconds between requests to the same domain.
        """
        self.delay = delay
        self.last_access: dict[str, float] = {}

    def wait(self, url: str) -> None:
        """Wait if necessary to respect rate limit for the domain.

        Args:
            url: URL being accessed
        """
        domain = urlparse(url).netloc
        if not domain:
            return

        now = time.time()
        last = self.last_access.get(domain, 0.0)

        elapsed = now - last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

        self.last_access[domain] = time.time()
