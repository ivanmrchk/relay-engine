"""Retries a request through brief outages and rate limiting.

A request is retried when it got no response at all (a timeout, a dropped
connection) or one that means "try again later": 429, or a 5xx server or
gateway error. Any other response goes back to the caller as is; asking
again won't turn a 404 into a 200.

The wait before each retry is the source's own Retry-After when it sends
one, and exponential backoff with jitter otherwise.

This is a function the caller hands its request to, not a decorator: a
decorator would fix `sleep` and `random_source` at import time, and passing
them in per call is what lets tests run instantly with predictable delays.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass

import httpx

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0


def send_with_retries(
    send_request: Callable[[], httpx.Response],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], None],
    random_source: random.Random,
) -> httpx.Response:
    """The first response not worth retrying, or the last one once attempts run out.

    If no attempt got a response at all, the last attempt's error is raised.
    """
    attempt = 1
    while True:
        is_last_attempt = attempt == policy.max_attempts
        try:
            response = send_request()
        except httpx.TransportError:
            if is_last_attempt:
                raise
            delay = _backoff_delay(attempt, policy, random_source)
        else:
            if is_last_attempt or response.status_code not in _RETRYABLE_STATUS_CODES:
                return response
            delay = _retry_after_seconds(response)
            if delay is None:
                delay = _backoff_delay(attempt, policy, random_source)
            elif delay > policy.max_delay_seconds:
                # Waiting that long is the scheduler's job: the next scheduled run picks this up.
                return response
        sleep(delay)
        attempt += 1


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """The source's Retry-After in seconds, or None if it sent none.

    Retry-After may also be an HTTP date. Only the seconds form is read: a
    date falls back to backoff, which still waits, just not the exact time.
    """
    try:
        return max(0.0, float(response.headers.get("Retry-After", "")))
    except ValueError:
        return None


def _backoff_delay(attempt: int, policy: RetryPolicy, random_source: random.Random) -> float:
    # Full jitter: anywhere from zero up to the exponential ceiling, so
    # requests that failed together don't all retry at the same moment.
    ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * 2 ** (attempt - 1))
    return random_source.uniform(0, ceiling)
