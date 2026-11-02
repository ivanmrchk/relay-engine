import pytest

from relay_engine.core.rate_limit import RateLimit, TokenBucket


def test_the_first_burst_goes_out_without_waiting(clock):
    bucket = TokenBucket(RateLimit(requests=1, per_seconds=1, burst=3), clock=clock, sleep=clock.sleep)

    for _ in range(3):
        bucket.acquire()

    assert clock.sleeps == []


def test_an_empty_bucket_waits_for_the_next_token(clock):
    bucket = TokenBucket(RateLimit(requests=60, per_seconds=60), clock=clock, sleep=clock.sleep)

    bucket.acquire()
    bucket.acquire()
    bucket.acquire()

    assert clock.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]


def test_time_spent_elsewhere_refills_the_bucket(clock):
    bucket = TokenBucket(RateLimit(requests=1, per_seconds=2), clock=clock, sleep=clock.sleep)
    bucket.acquire()

    clock.now += 2  # the caller was busy for a full refill interval
    bucket.acquire()

    assert clock.sleeps == []


def test_a_long_idle_spell_never_saves_up_more_than_the_burst(clock):
    bucket = TokenBucket(RateLimit(requests=1, per_seconds=1, burst=2), clock=clock, sleep=clock.sleep)

    clock.now += 1_000
    for _ in range(3):
        bucket.acquire()

    assert clock.sleeps == [pytest.approx(1.0)]
