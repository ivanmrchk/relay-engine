"""Against a fake of Google's token endpoint and Search Analytics query,
shaped like the documented API. Queries and pages are reserved examples.
"""

import base64
import json
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from relay_engine.clients.search_console import (
    DayDoesNotFitOneFile,
    days_to_fetch,
    fetch_day_pages,
)
from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.retry import RetryPolicy
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName

SITE_URL = "sc-domain:example.com"
DAYS = [date(2026, 10, 1), date(2026, 10, 2)]


class FakeGoogle:
    """An httpx transport for the token endpoint and the Search Analytics query."""

    def __init__(self, *, rows_per_day: int = 2) -> None:
        self._rows_per_day = rows_per_day
        self.requests: list[httpx.Request] = []
        self.sent_bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "oauth2.example":
            return httpx.Response(200, json={"access_token": "test-access-token", "expires_in": 3600})

        query = json.loads(request.content)
        rows = [
            {"keys": [f"example query {row}", "https://www.example.com/"], "clicks": 1, "impressions": 10}
            for row in range(self._rows_per_day)
        ]
        self.sent_bodies.append(json.dumps({"rows": rows, "responseAggregationType": "byPage"}).encode())
        assert query["startDate"] == query["endDate"]
        return httpx.Response(200, content=self.sent_bodies[-1])

    @property
    def queries(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host != "oauth2.example"]


def _pages(google, feed, clock, jitter_at_ceiling, service_account_info, days=DAYS):
    http = httpx.Client(base_url="https://www.googleapis.com", transport=httpx.MockTransport(google))
    session = ResilientSession(
        http,
        rate_limit=RateLimit(requests=100, per_seconds=1),
        retry_policy=RetryPolicy(max_attempts=3),
        clock=clock,
        sleep=clock.sleep,
        random_source=jitter_at_ceiling,
    )
    return fetch_day_pages(
        session,
        feed,
        site_url=SITE_URL,
        service_account_info=service_account_info,
        issued_at=1_790_000_000,
        days=days,
    )


@pytest.fixture
def pages(clock, jitter_at_ceiling, service_account_info):
    def pages(google, feed=FeedName.SEARCH_CONSOLE_QUERY_PAGES):
        return list(_pages(google, feed, clock, jitter_at_ceiling, service_account_info))

    return pages


def _jwt_claims(assertion: str) -> dict:
    payload = assertion.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def test_the_token_is_exchanged_for_a_signed_read_only_assertion(pages, service_account_info):
    google = FakeGoogle()

    pages(google)

    token_request = google.requests[0]
    form = parse_qs(token_request.content.decode())
    assert str(token_request.url) == "https://oauth2.example/token"
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    claims = _jwt_claims(form["assertion"][0])
    assert claims["iss"] == service_account_info["client_email"]
    assert claims["aud"] == "https://oauth2.example/token"
    assert claims["scope"] == "https://www.googleapis.com/auth/webmasters.readonly"
    assert claims["exp"] - claims["iat"] == 3600


def test_the_token_is_fetched_once_and_sent_with_every_query(pages):
    google = FakeGoogle()

    pages(google)

    assert len(google.requests) == 1 + len(DAYS)
    assert all(query.headers["Authorization"] == "Bearer test-access-token" for query in google.queries)


def test_one_query_per_day_named_after_that_day(pages):
    google = FakeGoogle()

    fetched = pages(google)

    assert [page.file_name for page in fetched] == ["2026-10-01.json", "2026-10-02.json"]
    asked_for = [json.loads(query.content)["startDate"] for query in google.queries]
    assert asked_for == ["2026-10-01", "2026-10-02"]


@pytest.mark.parametrize(
    ("feed", "dimensions"),
    [(FeedName.SEARCH_CONSOLE_TOTALS, ["date"]), (FeedName.SEARCH_CONSOLE_QUERY_PAGES, ["query", "page"])],
)
def test_each_feed_groups_by_its_own_dimensions(pages, feed, dimensions):
    google = FakeGoogle()

    pages(google, feed)

    assert json.loads(google.queries[0].content)["dimensions"] == dimensions


def test_the_domain_property_is_escaped_into_the_path(pages):
    google = FakeGoogle()

    pages(google)

    assert google.queries[0].url.raw_path.decode() == "/webmasters/v3/sites/sc-domain%3Aexample.com/searchAnalytics/query"


def test_each_response_is_passed_on_byte_for_byte(pages):
    google = FakeGoogle()

    fetched = pages(google)

    assert [page.content for page in fetched] == google.sent_bodies


def test_a_day_with_more_rows_than_one_response_holds_stops_the_pull(pages):
    with pytest.raises(DayDoesNotFitOneFile):
        pages(FakeGoogle(rows_per_day=25_000))


def test_nothing_is_requested_until_the_first_page_is_wanted(clock, jitter_at_ceiling, service_account_info):
    google = FakeGoogle()

    _pages(google, FeedName.SEARCH_CONSOLE_TOTALS, clock, jitter_at_ceiling, service_account_info)

    assert google.requests == []


def test_days_run_from_since_through_yesterday():
    days = days_to_fetch(date(2026, 10, 20), today=date(2026, 11, 3))

    assert (days[0], days[-1], len(days)) == (date(2026, 10, 20), date(2026, 11, 2), 14)


def test_a_recent_since_still_reaches_back_over_days_not_yet_final():
    days = days_to_fetch(date(2026, 11, 2), today=date(2026, 11, 3))

    assert days[0] == date(2026, 10, 30)


def test_without_since_it_goes_back_as_far_as_search_console_keeps():
    days = days_to_fetch(None, today=date(2026, 11, 3))

    assert days[0] == date(2025, 7, 11)  # 480 days: a little under 16 months
    assert days[-1] == date(2026, 11, 2)
