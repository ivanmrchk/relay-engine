import httpx
import pytest

from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.retry import RetryPolicy
from relay_engine.core.session import ResilientSession


class FakeSource:
    """An httpx transport that answers with each status in turn and records every request it got."""

    def __init__(self, *statuses: int, body: bytes = b"{}") -> None:
        self._statuses = list(statuses)
        self._body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._statuses.pop(0), content=self._body)


def _session(source, clock, jitter_at_ceiling, rate_limit=RateLimit(requests=1, per_seconds=2)):
    http = httpx.Client(base_url="https://source.example", transport=httpx.MockTransport(source))
    return ResilientSession(
        http,
        rate_limit=rate_limit,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=1, max_delay_seconds=10),
        clock=clock,
        sleep=clock.sleep,
        random_source=jitter_at_ceiling,
    )


def test_the_body_comes_back_byte_for_byte(clock, jitter_at_ceiling):
    body = b'{"leads":  [ ],\n "total": 0}'  # odd spacing that re-serializing would lose
    session = _session(FakeSource(200, body=body), clock, jitter_at_ceiling)

    assert session.request("GET", "/leads").content == body


def test_options_reach_the_source_unchanged(clock, jitter_at_ceiling):
    source = FakeSource(200)
    session = _session(source, clock, jitter_at_ceiling)

    session.request("GET", "/leads", params={"page": 2, "per_page": 100})

    assert str(source.requests[0].url) == "https://source.example/leads?page=2&per_page=100"


def test_requests_are_spaced_by_the_rate_limit(clock, jitter_at_ceiling):
    session = _session(FakeSource(200, 200), clock, jitter_at_ceiling)

    session.request("GET", "/leads")
    session.request("GET", "/leads")

    assert clock.sleeps == [pytest.approx(2.0)]


def test_a_retry_still_waits_its_turn_at_the_rate_limit(clock, jitter_at_ceiling):
    source = FakeSource(503, 200)
    session = _session(source, clock, jitter_at_ceiling)

    session.request("GET", "/leads")

    # 1 s of backoff, then the 1 s still owed to the rate limit (one request per 2 s).
    assert clock.sleeps == [1, pytest.approx(1.0)]
    assert len(source.requests) == 2


def test_a_client_error_raises_without_retrying(clock, jitter_at_ceiling):
    source = FakeSource(404)
    session = _session(source, clock, jitter_at_ceiling)

    with pytest.raises(httpx.HTTPStatusError):
        session.request("GET", "/leads")
    assert len(source.requests) == 1


def test_an_outage_that_outlasts_the_retries_raises(clock, jitter_at_ceiling):
    source = FakeSource(503, 503, 503)
    session = _session(source, clock, jitter_at_ceiling)

    with pytest.raises(httpx.HTTPStatusError):
        session.request("GET", "/leads")
    assert len(source.requests) == 3
