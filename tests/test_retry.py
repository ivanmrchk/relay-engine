import random

import httpx
import pytest

from relay_engine.core.retry import RetryPolicy, send_with_retries

POLICY = RetryPolicy(max_attempts=4, base_delay_seconds=1, max_delay_seconds=10)


class TopOfRange:
    """A random source that always picks the jitter ceiling, so delays are exact."""

    def uniform(self, low: float, high: float) -> float:
        return high


def _replies(*outcomes):
    """A send_request that answers with each outcome in turn; an exception is raised instead of returned."""
    remaining = list(outcomes)

    def send_request() -> httpx.Response:
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return send_request


def _send(send_request, clock, policy=POLICY):
    return send_with_retries(send_request, policy=policy, sleep=clock.sleep, random_source=TopOfRange())


def test_a_success_returns_at_once(clock):
    response = _send(_replies(httpx.Response(200)), clock)

    assert response.status_code == 200
    assert clock.sleeps == []


def test_server_errors_back_off_exponentially_until_one_succeeds(clock):
    replies = _replies(httpx.Response(503), httpx.Response(502), httpx.Response(500), httpx.Response(200))

    response = _send(replies, clock)

    assert response.status_code == 200
    assert clock.sleeps == [1, 2, 4]


def test_backoff_never_waits_longer_than_the_maximum(clock):
    policy = RetryPolicy(max_attempts=6, base_delay_seconds=4, max_delay_seconds=10)

    _send(_replies(*[httpx.Response(503)] * 5, httpx.Response(200)), clock, policy)

    assert clock.sleeps == [4, 8, 10, 10, 10]


def test_jitter_waits_somewhere_between_zero_and_the_ceiling(clock):
    replies = _replies(httpx.Response(503), httpx.Response(503), httpx.Response(200))

    send_with_retries(replies, policy=POLICY, sleep=clock.sleep, random_source=random.Random(7))

    first, second = clock.sleeps
    assert 0 <= first <= 1
    assert 0 <= second <= 2


def test_the_sources_retry_after_wins_over_backoff(clock):
    response = _send(_replies(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)), clock)

    assert response.status_code == 200
    assert clock.sleeps == [7]


def test_a_retry_after_longer_than_the_maximum_is_left_to_the_next_run(clock):
    response = _send(_replies(httpx.Response(429, headers={"Retry-After": "3600"})), clock)

    assert response.status_code == 429
    assert clock.sleeps == []


def test_a_retry_after_date_falls_back_to_backoff(clock):
    rate_limited = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    _send(_replies(rate_limited, httpx.Response(200)), clock)

    assert clock.sleeps == [1]


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(clock, status_code):
    response = _send(_replies(httpx.Response(status_code)), clock)

    assert response.status_code == status_code
    assert clock.sleeps == []


def test_the_last_response_comes_back_once_attempts_run_out(clock):
    response = _send(_replies(*[httpx.Response(503)] * 4), clock)

    assert response.status_code == 503
    assert clock.sleeps == [1, 2, 4]


def test_a_dropped_connection_is_retried(clock):
    response = _send(_replies(httpx.ConnectError("connection reset"), httpx.Response(200)), clock)

    assert response.status_code == 200
    assert clock.sleeps == [1]


def test_the_last_connection_error_is_raised_once_attempts_run_out(clock):
    with pytest.raises(httpx.ReadTimeout):
        _send(_replies(*[httpx.ReadTimeout("timed out")] * 4), clock)

    assert clock.sleeps == [1, 2, 4]
