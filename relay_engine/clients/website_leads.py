"""Website leads from the Surge Quick Quote Leads WordPress plugin.

GET /wp-json/surge-quick-quote-leads/v1/leads returns
{"total": N, "page": N, "per_page": N, "leads": [...]}, newest lead first.
Each page is passed on exactly as received; the body is read only for
`total` and `per_page`, to know how many pages there are.
"""

import math
from collections.abc import Iterator
from datetime import date

import httpx

from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FetchedPage

LEADS_PATH = "/wp-json/surge-quick-quote-leads/v1/leads"

# The plugin sets no rate limit (includes/Rest/LeadsController.php has no
# throttling), and neither does the WordPress REST API. One request a second
# is our own ceiling, not a documented one: the site runs on shared hosting
# that also serves the business's visitors.
WEBSITE_LEADS_RATE_LIMIT = RateLimit(requests=1, per_seconds=1)

_LARGEST_PAGE_THE_PLUGIN_SERVES = 100


def website_leads_http_client(*, site_url: str, leads_key: str) -> httpx.Client:
    return httpx.Client(base_url=site_url, headers={"X-Surge-QQ-Leads-Key": leads_key}, timeout=30.0)


def fetch_lead_pages(session: ResilientSession, *, date_from: date | None) -> Iterator[FetchedPage]:
    """Every page of leads created on or after `date_from` (all leads if None), each yielded as soon as it's fetched.

    `date_from` is a day, not a moment: the plugin compares it against
    created_at in the site's local time, and a whole day leaves no timezone
    to get wrong. Refetching part of a day is harmless, since bi-engine keeps
    one row per lead id.
    """
    page_number, total_pages = 1, 1
    while page_number <= total_pages:
        response = session.request("GET", LEADS_PATH, params=_query(page_number, date_from))
        total_pages = _total_pages_from_body(response.json())
        yield FetchedPage(f"page_{page_number:03d}.json", response.content)
        page_number += 1


def _query(page_number: int, date_from: date | None) -> dict[str, str | int]:
    query: dict[str, str | int] = {"page": page_number, "per_page": _LARGEST_PAGE_THE_PLUGIN_SERVES}
    if date_from is not None:
        query["date_from"] = date_from.isoformat()
    return query


def _total_pages_from_body(body: dict) -> int:
    # Computed from the body rather than read from X-WP-TotalPages: a proxy
    # in front of the site may strip that header. It's the per_page the
    # server actually used, not the one we asked for, in case its cap ever
    # drops below 100. Recomputed on every page, because a lead that arrives
    # mid-pull raises the total; being newest-first, it also pushes one
    # already-fetched lead onto the next page, a repeat bi-engine dedups.
    return math.ceil(body["total"] / body["per_page"])
