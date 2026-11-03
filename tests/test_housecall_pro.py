"""Against a fake of Housecall Pro's list endpoints, shaped like the live API's
responses (structure confirmed 2026-11-03). Records carry ids only.
"""

import json
import math

import httpx
import pytest

from relay_engine.clients.housecall_pro import (
    HOUSECALL_PRO_PATHS,
    fetch_feed_pages,
    housecall_pro_http_client,
)
from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.retry import RetryPolicy
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName

LIST_KEYS = {"/jobs": "jobs", "/invoices": "invoices", "/estimates": "estimates"}


class FakeHousecallPro:
    """An httpx transport that pages `record_count` records on every list endpoint."""

    def __init__(self, record_count: int) -> None:
        self._record_count = record_count
        self.requests: list[httpx.Request] = []
        self.sent_bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = int(request.url.params["page"])
        page_size = int(request.url.params["page_size"])
        first_id = (page - 1) * page_size + 1
        record_ids = range(first_id, min(first_id + page_size, self._record_count + 1))
        body = {
            LIST_KEYS[request.url.path]: [{"id": f"record_{record_id}"} for record_id in record_ids],
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(self._record_count / page_size),
            "total_items": self._record_count,
        }
        self.sent_bodies.append(json.dumps(body).encode())
        return httpx.Response(200, content=self.sent_bodies[-1])


def _pages(housecall_pro, feed, clock, jitter_at_ceiling):
    http = httpx.Client(base_url="https://api.housecall.example", transport=httpx.MockTransport(housecall_pro))
    session = ResilientSession(
        http,
        rate_limit=RateLimit(requests=100, per_seconds=1),
        retry_policy=RetryPolicy(max_attempts=3),
        clock=clock,
        sleep=clock.sleep,
        random_source=jitter_at_ceiling,
    )
    return fetch_feed_pages(session, feed)


@pytest.fixture
def pages(clock, jitter_at_ceiling):
    def pages(housecall_pro, feed=FeedName.HOUSECALL_PRO_JOBS):
        return list(_pages(housecall_pro, feed, clock, jitter_at_ceiling))

    return pages


@pytest.mark.parametrize("feed", list(HOUSECALL_PRO_PATHS))
def test_each_feed_reads_its_own_endpoint(pages, feed):
    housecall_pro = FakeHousecallPro(5)

    pages(housecall_pro, feed)

    assert housecall_pro.requests[0].url.path == HOUSECALL_PRO_PATHS[feed]


def test_pages_until_the_stated_total_pages(pages):
    fetched = pages(FakeHousecallPro(250))

    assert [page.file_name for page in fetched] == ["page_001.json", "page_002.json", "page_003.json"]


def test_asks_for_a_hundred_per_page(pages):
    housecall_pro = FakeHousecallPro(5)

    pages(housecall_pro)

    assert housecall_pro.requests[0].url.params["page_size"] == "100"


def test_each_page_is_passed_on_byte_for_byte(pages):
    housecall_pro = FakeHousecallPro(150)

    fetched = pages(housecall_pro)

    assert [page.content for page in fetched] == housecall_pro.sent_bodies


def test_an_empty_feed_is_still_one_page(pages):
    fetched = pages(FakeHousecallPro(0))

    assert [page.file_name for page in fetched] == ["page_001.json"]


def test_each_page_is_handed_over_before_the_next_is_fetched(clock, jitter_at_ceiling):
    housecall_pro = FakeHousecallPro(250)
    lazy_pages = _pages(housecall_pro, FeedName.HOUSECALL_PRO_JOBS, clock, jitter_at_ceiling)

    next(lazy_pages)

    assert len(housecall_pro.requests) == 1


def test_the_http_client_sends_the_token_as_a_bearer():
    with housecall_pro_http_client(base_url="https://api.housecall.example", api_token="test-token") as http:
        assert http.headers["Authorization"] == "Bearer test-token"
