"""python -m relay_engine pull <feed> [--since DAY] | create-demo

URLs and credentials come from env vars, listed in .env.example. relay-engine
keeps no state between runs: the scheduler (n8n) passes --since, the day
before its last successful run, and a pull without it fetches everything.
"""

import argparse
import json
import os
import random
import sys
import time
from collections.abc import Iterator
from datetime import date

import httpx

from relay_engine.clients.housecall_pro import (
    HOUSECALL_PRO_PATHS,
    HOUSECALL_PRO_RATE_LIMIT,
    fetch_feed_pages,
    housecall_pro_http_client,
)
from relay_engine.clients.search_console import (
    SEARCH_CONSOLE_DIMENSIONS,
    SEARCH_CONSOLE_RATE_LIMIT,
    DayDoesNotFitOneFile,
    days_to_fetch,
    fetch_day_pages,
    search_console_http_client,
)
from relay_engine.clients.website_leads import (
    WEBSITE_LEADS_RATE_LIMIT,
    fetch_lead_pages,
    website_leads_http_client,
)
from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.retry import RetryPolicy
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName, FetchedPage
from relay_engine.writers.bi_engine import (
    BI_ENGINE_RATE_LIMIT,
    DeliveryResult,
    bi_engine_http_client,
    deliver_pull,
)

# Housecall Pro has no filter relay-engine uses, so its pulls are always full.
_FEEDS_TAKING_SINCE = (FeedName.WEBSITE_LEADS, *SEARCH_CONSOLE_DIMENSIONS)


class UnusableSetting(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    if arguments.command == "create-demo":
        print("create-demo isn't built yet", file=sys.stderr)
        return 1

    try:
        result = _pull(arguments.feed, since=arguments.since)
    except UnusableSetting as error:
        print(error, file=sys.stderr)
        return 1
    except (httpx.HTTPError, DayDoesNotFitOneFile) as error:  # final, after the retries; the next run tries again
        print(f"pull {arguments.feed} failed: {error}", file=sys.stderr)
        return 1

    print(f"{result.feed}: uploaded {result.pages_uploaded} page(s), {result.new_pages} new to bi-engine")
    return 0


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m relay_engine")
    commands = parser.add_subparsers(dest="command", required=True)

    pull = commands.add_parser("pull", help="fetch one feed from its source and deliver it to bi-engine")
    pull.add_argument("feed", choices=list(FeedName))
    pull.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD", help="only records from this day on")

    commands.add_parser("create-demo", help="AI drafts a record, then creates it via API")
    arguments = parser.parse_args(argv)

    if arguments.command != "pull":
        return arguments
    arguments.feed = FeedName(arguments.feed)  # argparse checked it against the choices, but hands back a str
    if arguments.since and arguments.feed not in _FEEDS_TAKING_SINCE:
        parser.error(f"--since doesn't apply to {arguments.feed}")
    return arguments


def _pull(feed: FeedName, *, since: date | None) -> DeliveryResult:
    """Fetch one feed from its source and deliver it to bi-engine, page by page."""
    bi_engine_url, relay_ingest_key = _setting("BI_ENGINE_URL"), _setting("RELAY_INGEST_KEY")
    source_http, pages = _connect_to_source(feed, since=since)
    with (
        source_http,
        bi_engine_http_client(base_url=bi_engine_url, relay_ingest_key=relay_ingest_key) as bi_engine_http,
    ):
        return deliver_pull(_resilient(bi_engine_http, BI_ENGINE_RATE_LIMIT), feed, pages)


def _connect_to_source(feed: FeedName, *, since: date | None) -> tuple[httpx.Client, Iterator[FetchedPage]]:
    """The source's HTTP client, and its pages: a generator, so nothing is fetched until delivery starts."""
    if feed is FeedName.WEBSITE_LEADS:
        http = website_leads_http_client(
            site_url=_setting("WEBSITE_LEADS_SITE_URL"), leads_key=_setting("WEBSITE_LEADS_KEY")
        )
        return http, fetch_lead_pages(_resilient(http, WEBSITE_LEADS_RATE_LIMIT), date_from=since)

    if feed in SEARCH_CONSOLE_DIMENSIONS:
        site_url, service_account_info = _setting("SEARCH_CONSOLE_SITE_URL"), _service_account_info()
        http = search_console_http_client()
        return http, fetch_day_pages(
            _resilient(http, SEARCH_CONSOLE_RATE_LIMIT),
            feed,
            site_url=site_url,
            service_account_info=service_account_info,
            issued_at=int(time.time()),
            days=days_to_fetch(since, today=date.today()),
        )

    http = housecall_pro_http_client(base_url=_setting("HCP_BASE_URL"), api_token=_setting("HCP_API_TOKEN"))
    return http, fetch_feed_pages(_resilient(http, HOUSECALL_PRO_RATE_LIMIT), feed)


def _resilient(http: httpx.Client, rate_limit: RateLimit) -> ResilientSession:
    return ResilientSession(
        http,
        rate_limit=rate_limit,
        retry_policy=RetryPolicy(),
        clock=time.monotonic,
        sleep=time.sleep,
        random_source=random.Random(),
    )


def _service_account_info() -> dict:
    name = "SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON"
    try:
        return json.loads(_setting(name))
    except json.JSONDecodeError:  # its message gives a position, never the key itself
        raise UnusableSetting(f"{name} isn't valid JSON; it should hold the key file's contents") from None


def _setting(name: str) -> str:
    """An env var's value; only its name ever appears in an error, since most are credentials."""
    value = os.environ.get(name, "")
    if not value:
        raise UnusableSetting(f"{name} isn't set; see .env.example")
    return value
