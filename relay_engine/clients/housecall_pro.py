"""Jobs, invoices and estimates from the Housecall Pro public API.

GET /jobs, /invoices and /estimates each return
{"<jobs|invoices|estimates>": [...], "page": N, "page_size": N,
"total_pages": N, "total_items": N}, confirmed against the live API on
2026-11-03 (structure only; no record was read). Each page is passed on
exactly as received; the body is read only for `total_pages`.

There's no incremental filter in use, so every pull is a full one. That's
what makes missed records self-healing: invoices come back in no stable
order, so one that moves between pages mid-pull is caught by the next run,
and bi-engine keeps only each record's latest version.
"""

from collections.abc import Iterator

import httpx

from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName, FetchedPage

HOUSECALL_PRO_PATHS = {
    FeedName.HOUSECALL_PRO_JOBS: "/jobs",
    FeedName.HOUSECALL_PRO_INVOICES: "/invoices",
    FeedName.HOUSECALL_PRO_ESTIMATES: "/estimates",
}

# Housecall Pro publishes no number, but every response states its limit:
# `ratelimit-limit: 300` per fixed 60-second window (`ratelimit-reset` counts
# down from 60), counted per endpoint; observed 2026-11-03. 240 leaves a
# fifth of each window for the lead-relay Worker, which uses the same token.
HOUSECALL_PRO_RATE_LIMIT = RateLimit(requests=240, per_seconds=60)

# The size the lead-relay Worker already uses against the live API; 500 is refused.
_PAGE_SIZE = 100


def housecall_pro_http_client(*, base_url: str, api_token: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_token}"}, timeout=30.0)


def fetch_feed_pages(session: ResilientSession, feed: FeedName) -> Iterator[FetchedPage]:
    """Every page of one Housecall Pro feed, each yielded as soon as it's fetched."""
    path = HOUSECALL_PRO_PATHS[feed]
    page_number, total_pages = 1, 1
    while page_number <= total_pages:
        response = session.request("GET", path, params={"page": page_number, "page_size": _PAGE_SIZE})
        total_pages = response.json()["total_pages"]  # unlike the leads plugin, Housecall Pro states it
        yield FetchedPage(f"page_{page_number:03d}.json", response.content)
        page_number += 1
