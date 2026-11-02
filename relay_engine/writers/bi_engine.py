"""Delivers a pull's pages to bi-engine, one upload per page, then one rebuild.

Each page goes to POST /ingest/{feed_name} as soon as it's fetched, with
?rebuild=false, and the pull ends with a single POST /ingest/rebuild: a
rebuild takes about 20 seconds, too long to repeat after every page.
"""

from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from relay_engine.core.idempotency import idempotency_key
from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName, FetchedPage

# bi-engine is ours and documents no limit; this only keeps a long pull
# from crowding its single small Fly machine while the dashboard is in use.
BI_ENGINE_RATE_LIMIT = RateLimit(requests=5, per_seconds=1)

# A rebuild holds the response for about 20 seconds; the read timeout leaves room for that.
_TIMEOUT = httpx.Timeout(10.0, read=120.0)


@dataclass(frozen=True)
class DeliveryResult:
    feed: FeedName
    pages_uploaded: int
    new_pages: int


def bi_engine_http_client(*, base_url: str, relay_ingest_key: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"X-Relay-Ingest-Key": relay_ingest_key}, timeout=_TIMEOUT)


def deliver_pull(session: ResilientSession, feed: FeedName, pages: Iterable[FetchedPage]) -> DeliveryResult:
    """Upload each page as it arrives from `pages`, then rebuild bi-engine's clean layer once."""
    pages_uploaded, new_pages = 0, 0
    for page in pages:
        if _upload_page(session, feed, page):
            new_pages += 1
        pages_uploaded += 1

    # Rebuild even when no upload came back new: if a page was stored but its
    # response was lost, the retry finds it already there and says was_new
    # false, for a page that's in no rebuild yet.
    if pages_uploaded:
        session.request("POST", "/ingest/rebuild")
    return DeliveryResult(feed, pages_uploaded, new_pages)


def _upload_page(session: ResilientSession, feed: FeedName, page: FetchedPage) -> bool:
    """Whether bi-engine hadn't stored these exact bytes for this feed before."""
    response = session.request(
        "POST",
        f"/ingest/{feed}",
        params={"rebuild": "false"},
        files={"file": (page.file_name, page.content, "application/json")},
        # bi-engine doesn't read this yet; its content-hash dedup is what makes a retry safe.
        headers={"Idempotency-Key": idempotency_key(feed, page.file_name, page.content)},
    )
    return response.json()["was_new"]
