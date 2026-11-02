import random

import httpx
import pytest

from relay_engine.core.retry import RetryPolicy, send_with_retries

POLICY = RetryPolicy(max_attempts=4, base_delay_seconds=1, max_delay_seconds=10)


@pytest.fixture
def send(clock, jitter_at_ceiling):
    def send(send_request, policy=POLICY):
        return send_with_retries(send_request, policy=policy, sleep=clock.sleep, random_source=jitter_at_ceiling)

    return send


def _replies(*outcomes):
    """A send_request that answers with each outcome in turn; an exception is raised instead of returned."""
    remaining = list(outcomes)

    def send_request() -> httpx.Response:
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return send_request


def test_a_success_returns_at_once(send, clock):
    response = send(_replies(httpx.Response(200)))

    assert response.status_code == 200
    assert clock.sleeps == []


def test_server_errors_back_off_exponentially_until_one_succeeds(send, clock):
    replies = _replies(httpx.Response(503), httpx.Response(502), httpx.Response(500), httpx.Response(200))

    response = send(replies)

    assert response.status_code == 200
    assert clock.sleeps == [1, 2, 4]


def test_backoff_never_waits_longer_than_the_maximum(send, clock):
    policy = RetryPolicy(max_attempts=6, base_delay_seconds=4, max_delay_seconds=10)

    send(_replies(*[httpx.Response(503)] * 5, httpx.Response(200)), policy)

    assert clock.sleeps == [4, 8, 10, 10, 10]


def test_jitter_waits_somewhere_between_zero_and_the_ceiling(clock):
    replies = _replies(httpx.Response(503), httpx.Response(503), httpx.Response(200))

    send_with_retries(replies, policy=POLICY, sleep=clock.sleep, random_source=random.Random(7))

    first, second = clock.sleeps
    assert 0 <= first <= 1
    assert 0 <= second <= 2


def test_the_sources_retry_after_wins_over_backoff(send, clock):
    response = send(_replies(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)))

    assert response.status_code == 200
    assert clock.sleeps == [7]


def test_a_retry_after_longer_than_the_maximum_is_left_to_the_next_run(send, clock):
    response = send(_replies(httpx.Response(429, headers={"Retry-After": "3600"})))

    assert response.status_code == 429
    assert clock.sleeps == []


def test_a_retry_after_date_falls_back_to_backoff(send, clock):
    rate_limited = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    send(_replies(rate_limited, httpx.Response(200)))

    assert clock.sleeps == [1]


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(send, clock, status_code):
    response = send(_replies(httpx.Response(status_code)))

    assert response.status_code == status_code
    assert clock.sleeps == []


def test_the_last_response_comes_back_once_attempts_run_out(send, clock):
    response = send(_replies(*[httpx.Response(503)] * 4))

    assert response.status_code == 503
    assert clock.sleeps == [1, 2, 4]


def test_a_dropped_connection_is_retried(send, clock):
    response = send(_replies(httpx.ConnectError("connection reset"), httpx.Response(200)))

    assert response.status_code == 200
    assert clock.sleeps == [1]


def test_the_last_connection_error_is_raised_once_attempts_run_out(send, clock):
    with pytest.raises(httpx.ReadTimeout):
        send(_replies(*[httpx.ReadTimeout("timed out")] * 4))

    assert clock.sleeps == [1, 2, 4]
