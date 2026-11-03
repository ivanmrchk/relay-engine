"""Against a fake of the plugin's GET /leads, built from LeadsController.php:
newest first, LIMIT/OFFSET paging, per_page capped at 100. Every lead uses
reserved example values only.
"""

import json
from datetime import date

import httpx
import pytest

from relay_engine.clients.website_leads import (
    LEADS_PATH,
    fetch_lead_pages,
    website_leads_http_client,
)
from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.retry import RetryPolicy
from relay_engine.core.session import ResilientSession


def _example_lead(lead_id: int) -> dict:
    return {
        "id": lead_id,
        "name": f"Example Lead {lead_id}",
        "phone": "(425) 555-0100",
        "email": f"lead{lead_id}@example.com",
        "ip_address": "192.0.2.10",
        "created_at": "2026-10-01 09:30:00",
    }


class FakeLeadsPlugin:
    """An httpx transport that pages leads the way the plugin does."""

    def __init__(self, lead_count: int, *, per_page_cap: int = 100, total_pages_header: str | None = None) -> None:
        self._lead_ids = list(range(lead_count, 0, -1))  # newest first: ORDER BY id DESC
        self._per_page_cap = per_page_cap
        self._total_pages_header = total_pages_header
        self.requests: list[httpx.Request] = []
        self.sent_bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = int(request.url.params["page"])
        per_page = min(int(request.url.params["per_page"]), self._per_page_cap)
        offset = (page - 1) * per_page
        body = {
            "total": len(self._lead_ids),
            "page": page,
            "per_page": per_page,
            "leads": [_example_lead(lead_id) for lead_id in self._lead_ids[offset : offset + per_page]],
        }
        headers = {"X-WP-TotalPages": self._total_pages_header} if self._total_pages_header else {}
        self.sent_bodies.append(json.dumps(body).encode())
        return httpx.Response(200, content=self.sent_bodies[-1], headers=headers)


def _pages(plugin, clock, jitter_at_ceiling, *, date_from=None):
    http = httpx.Client(base_url="https://www.site.example", transport=httpx.MockTransport(plugin))
    session = ResilientSession(
        http,
        rate_limit=RateLimit(requests=100, per_seconds=1),
        retry_policy=RetryPolicy(max_attempts=3),
        clock=clock,
        sleep=clock.sleep,
        random_source=jitter_at_ceiling,
    )
    return fetch_lead_pages(session, date_from=date_from)


@pytest.fixture
def pages(clock, jitter_at_ceiling):
    def pages(plugin, *, date_from=None):
        return list(_pages(plugin, clock, jitter_at_ceiling, date_from=date_from))

    return pages


def test_pages_until_every_lead_is_covered(pages):
    fetched = pages(FakeLeadsPlugin(250))

    assert [page.file_name for page in fetched] == ["page_001.json", "page_002.json", "page_003.json"]
    lead_ids = [lead["id"] for page in fetched for lead in json.loads(page.content)["leads"]]
    assert sorted(lead_ids) == list(range(1, 251))


def test_always_asks_for_the_largest_page_and_the_leads_endpoint(pages):
    plugin = FakeLeadsPlugin(5)

    pages(plugin)

    assert plugin.requests[0].url.path == LEADS_PATH
    assert plugin.requests[0].url.params["per_page"] == "100"


def test_follows_the_page_size_the_server_actually_used(pages):
    fetched = pages(FakeLeadsPlugin(120, per_page_cap=50))

    assert len(fetched) == 3


def test_ignores_a_wrong_total_pages_header(pages):
    fetched = pages(FakeLeadsPlugin(250, total_pages_header="1"))

    assert len(fetched) == 3


def test_each_page_is_passed_on_byte_for_byte(pages):
    plugin = FakeLeadsPlugin(150)

    fetched = pages(plugin)

    assert [page.content for page in fetched] == plugin.sent_bodies


def test_no_leads_is_still_one_page(pages):
    fetched = pages(FakeLeadsPlugin(0))

    assert [page.file_name for page in fetched] == ["page_001.json"]


def test_date_from_is_sent_as_a_day(pages):
    plugin = FakeLeadsPlugin(5)

    pages(plugin, date_from=date(2026, 9, 1))

    assert plugin.requests[0].url.params["date_from"] == "2026-09-01"


def test_no_date_from_asks_for_every_lead(pages):
    plugin = FakeLeadsPlugin(5)

    pages(plugin)

    assert "date_from" not in plugin.requests[0].url.params


def test_each_page_is_handed_over_before_the_next_is_fetched(clock, jitter_at_ceiling):
    plugin = FakeLeadsPlugin(250)
    lazy_pages = _pages(plugin, clock, jitter_at_ceiling)

    next(lazy_pages)

    assert len(plugin.requests) == 1


def test_the_http_client_sends_the_leads_key():
    with website_leads_http_client(site_url="https://www.site.example", leads_key="test-key") as http:
        assert http.headers["X-Surge-QQ-Leads-Key"] == "test-key"
