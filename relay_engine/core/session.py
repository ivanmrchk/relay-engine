"""The one place relay-engine makes HTTP calls, to a source or to bi-engine.

Every request first waits its turn at the source's rate limit, then is
retried through outages and 429s. A retry waits its turn too: retrying is
exactly when a source is most likely to be counting.
"""

import random
from collections.abc import Callable

import httpx

from relay_engine.core.rate_limit import RateLimit, TokenBucket
from relay_engine.core.retry import RetryPolicy, send_with_retries


class ResilientSession:
    def __init__(
        self,
        http: httpx.Client,
        *,
        rate_limit: RateLimit,
        retry_policy: RetryPolicy,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        random_source: random.Random,
    ) -> None:
        """`http` comes configured by the caller: base URL, auth headers, timeouts."""
        self._http = http
        self._bucket = TokenBucket(rate_limit, clock=clock, sleep=sleep)
        self._retry_policy = retry_policy
        self._sleep = sleep
        self._random_source = random_source

    def request(self, method: str, url: str, **httpx_options) -> httpx.Response:
        """The response, once it's a success; raises httpx.HTTPStatusError if it never becomes one.

        `httpx_options` go to httpx.Client.request unchanged (params, files, headers).
        """

        def send_request() -> httpx.Response:
            self._bucket.acquire()
            return self._http.request(method, url, **httpx_options)

        response = send_with_retries(
            send_request, policy=self._retry_policy, sleep=self._sleep, random_source=self._random_source
        )
        response.raise_for_status()
        return response
