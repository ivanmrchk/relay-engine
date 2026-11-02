import pytest


class FakeClock:
    """Stands in for time.monotonic and time.sleep: sleeping moves the clock forward instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TopOfRange:
    """A random source that always picks the jitter ceiling, so backoff delays are exact."""

    def uniform(self, low: float, high: float) -> float:
        return high


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def jitter_at_ceiling() -> TopOfRange:
    return TopOfRange()
