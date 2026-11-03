import pytest
import rsa


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


@pytest.fixture(scope="session")
def service_account_info() -> dict:
    """A throwaway service-account key, shaped like the JSON Google issues, made fresh for each test run."""
    _, private_key = rsa.newkeys(1024)
    return {
        "type": "service_account",
        "client_email": "relay@relay-engine-test.iam.example",
        "private_key_id": "test-key-id",
        "private_key": private_key.save_pkcs1().decode(),
        "token_uri": "https://oauth2.example/token",
    }
