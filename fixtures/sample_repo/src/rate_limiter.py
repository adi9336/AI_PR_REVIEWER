"""Rate limiting middleware for the sample repo.

Implements a token bucket rate limiter to prevent API abuse.
"""

from __future__ import annotations

import time
from collections import defaultdict


class TokenBucket:
    """Token bucket rate limiter."""

    def __init__(self, capacity: int, refill_rate: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def allow(self) -> bool:
        """Check if a request is allowed under the rate limit."""
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Per-client rate limiter using token buckets."""

    def __init__(self, capacity: int = 10, refill_rate: float = 1.0) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.buckets: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(capacity, refill_rate)
        )

    def check(self, client_id: str) -> bool:
        """Check if client_id is allowed to make a request."""
        return self.buckets[client_id].allow()