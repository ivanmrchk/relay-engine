"""python -m relay_engine pull <feed> [--since DAY] | create-demo

URLs and credentials come from env vars, listed in .env.example. relay-engine
keeps no state between runs: the scheduler (n8n) passes --since, the day
before its last successful run, and a pull without it fetches everything.
"""

import argparse
import os
import random
import sys
import time
from datetime import date

import httpx

from relay_engine.clients.website_leads import (
    WEBSITE_LEADS_RATE_LIMIT,
    fetch_lead_pages,
    website_leads_http_client,
)
from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.retry import RetryPolicy
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName
from relay_engine.writers.bi_engine import (
    BI_ENGINE_RATE_LIMIT,
    DeliveryResult,
    bi_engine_http_client,
    deliver_pull,
)


class MissingSetting(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    if arguments.command == "create-demo":
        return _not_built_yet("create-demo")
    if arguments.feed != FeedName.WEBSITE_LEADS:
        return _not_built_yet(f"pull {arguments.feed}")

    try:
        result = _pull_website_leads(since=arguments.since)
    except MissingSetting as error:
        print(error, file=sys.stderr)
        return 1
    except httpx.HTTPError as error:  # a final failure, after the retries; the next scheduled run tries again
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

    if arguments.command == "pull" and arguments.since and arguments.feed != FeedName.WEBSITE_LEADS:
        parser.error(f"--since doesn't apply to {arguments.feed}")
    return arguments


def _not_built_yet(command: str) -> int:
    print(f"{command} isn't built yet", file=sys.stderr)
    return 1


def _pull_website_leads(*, since: date | None) -> DeliveryResult:
    # Every setting is read before any connection opens, so a missing one fails fast.
    site_url, leads_key = _setting("WEBSITE_LEADS_SITE_URL"), _setting("WEBSITE_LEADS_KEY")
    bi_engine_url, relay_ingest_key = _setting("BI_ENGINE_URL"), _setting("RELAY_INGEST_KEY")

    with (
        website_leads_http_client(site_url=site_url, leads_key=leads_key) as leads_http,
        bi_engine_http_client(base_url=bi_engine_url, relay_ingest_key=relay_ingest_key) as bi_engine_http,
    ):
        pages = fetch_lead_pages(_resilient(leads_http, WEBSITE_LEADS_RATE_LIMIT), date_from=since)
        return deliver_pull(_resilient(bi_engine_http, BI_ENGINE_RATE_LIMIT), FeedName.WEBSITE_LEADS, pages)


def _resilient(http: httpx.Client, rate_limit: RateLimit) -> ResilientSession:
    return ResilientSession(
        http,
        rate_limit=rate_limit,
        retry_policy=RetryPolicy(),
        clock=time.monotonic,
        sleep=time.sleep,
        random_source=random.Random(),
    )


def _setting(name: str) -> str:
    """An env var's value; only its name ever appears in an error, since most are credentials."""
    value = os.environ.get(name, "")
    if not value:
        raise MissingSetting(f"{name} isn't set; see .env.example")
    return value
