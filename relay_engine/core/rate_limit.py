"""A token-bucket rate limiter: one instance per source, shared by its feeds.

Each request takes a token, and tokens refill at the source's documented
rate, up to `burst`. When the bucket is empty, acquire() sleeps until the
next token is due, so the source never has to answer 429 in the first place.

Not thread-safe: a pull runs in a single thread.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimit:
    """A source's limit, read from its docs and cited where the client defines it."""

    requests: int
    per_seconds: float
    burst: int = 1  # requests allowed back to back; 1 spaces every request evenly


class TokenBucket:
    def __init__(self, limit: RateLimit, *, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._refill_per_second = limit.requests / limit.per_seconds
        self._capacity = limit.burst
        self._tokens = float(limit.burst)
        self._clock = clock
        self._sleep = sleep
        self._last_refill = clock()

    def acquire(self) -> None:
        """Take one token, first sleeping until one is available."""
        self._refill()
        if self._tokens < 1:
            self._sleep((1 - self._tokens) / self._refill_per_second)
            self._refill()
        self._tokens -= 1

    def _refill(self) -> None:
        now = self._clock()
        earned = (now - self._last_refill) * self._refill_per_second
        self._tokens = min(self._capacity, self._tokens + earned)
        self._last_refill = now
