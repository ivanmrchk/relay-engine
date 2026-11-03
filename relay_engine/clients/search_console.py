"""Daily Google Search Console data: one request per day per feed.

POST /webmasters/v3/sites/{site}/searchAnalytics/query for a single day
(startDate = endDate), grouped by `date` alone for the day's totals, or by
`query` and `page`. Each response is passed on exactly as received, named
YYYY-MM-DD.json: the query/page rows don't repeat the date, so bi-engine
reads the day from the file name.

Authenticates as a service account: a JWT signed with its private key is
exchanged for an access token, through the same session as every other
call. google-auth only signs; it makes no HTTP calls of its own here.
"""

from collections.abc import Iterable, Iterator
from datetime import date, timedelta
from urllib.parse import quote

import httpx
from google.auth import crypt, jwt

from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName, FetchedPage

SEARCH_CONSOLE_DIMENSIONS = {
    FeedName.SEARCH_CONSOLE_TOTALS: ["date"],
    FeedName.SEARCH_CONSOLE_QUERY_PAGES: ["query", "page"],
}

# https://developers.google.com/webmaster-tools/limits: Search Analytics
# allows 1,200 queries per minute per site and per user. Half that leaves
# room for the separate load quota, which is measured in 10-minute chunks,
# publishes no number, and is spent fastest by query-and-page grouping.
SEARCH_CONSOLE_RATE_LIMIT = RateLimit(requests=600, per_seconds=60)

_READ_ONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
# An access token lasts an hour; a full 16-month pull takes a couple of minutes.
_TOKEN_LIFETIME_SECONDS = 3600

# The API's maximum rowLimit. A day that reaches it has more rows than one
# response holds, and bi-engine keeps a single file per day.
_MOST_ROWS_IN_ONE_RESPONSE = 25_000

# Search Console finalizes a day two to three days later; until then the
# day comes back empty. Every pull reaches back this far, so a day that was
# empty on one run is fetched again on the next.
_DAYS_STILL_SETTLING = 4
# Search Console keeps 16 months; a little under that never asks for a dropped day.
_DAYS_SEARCH_CONSOLE_KEEPS = 480


class DayDoesNotFitOneFile(Exception):
    pass


def search_console_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://www.googleapis.com", timeout=60.0)


def days_to_fetch(since: date | None, *, today: date) -> list[date]:
    """Every day from `since` (or as far back as Search Console keeps) through yesterday."""
    yesterday = today - timedelta(days=1)
    first_day = since or today - timedelta(days=_DAYS_SEARCH_CONSOLE_KEEPS)
    first_day = min(first_day, today - timedelta(days=_DAYS_STILL_SETTLING))
    return [first_day + timedelta(days=offset) for offset in range((yesterday - first_day).days + 1)]


def fetch_day_pages(
    session: ResilientSession,
    feed: FeedName,
    *,
    site_url: str,
    service_account_info: dict,
    issued_at: int,
    days: Iterable[date],
) -> Iterator[FetchedPage]:
    """One response per day, each yielded as soon as it's fetched; the token is fetched with the first."""
    access_token = _access_token(session, service_account_info, issued_at=issued_at)
    query_path = f"/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    for day in days:
        response = session.request(
            "POST",
            query_path,
            json=_query_for(feed, day),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        _check_the_day_fits_one_file(response.json(), day)
        yield FetchedPage(f"{day.isoformat()}.json", response.content)


def _access_token(session: ResilientSession, service_account_info: dict, *, issued_at: int) -> str:
    response = session.request(
        "POST",
        service_account_info["token_uri"],
        data={"grant_type": _JWT_BEARER_GRANT, "assertion": _signed_assertion(service_account_info, issued_at)},
    )
    return response.json()["access_token"]


def _signed_assertion(service_account_info: dict, issued_at: int) -> str:
    signer = crypt.RSASigner.from_service_account_info(service_account_info)
    claims = {
        "iss": service_account_info["client_email"],
        "scope": _READ_ONLY_SCOPE,
        "aud": service_account_info["token_uri"],
        "iat": issued_at,
        "exp": issued_at + _TOKEN_LIFETIME_SECONDS,
    }
    return jwt.encode(signer, claims).decode()


def _query_for(feed: FeedName, day: date) -> dict:
    # No dataState: the default is final data only, which never changes afterwards.
    return {
        "startDate": day.isoformat(),
        "endDate": day.isoformat(),
        "dimensions": SEARCH_CONSOLE_DIMENSIONS[feed],
        "rowLimit": _MOST_ROWS_IN_ONE_RESPONSE,
    }


def _check_the_day_fits_one_file(body: dict, day: date) -> None:
    if len(body.get("rows", [])) >= _MOST_ROWS_IN_ONE_RESPONSE:
        raise DayDoesNotFitOneFile(
            f"{day}: Search Console returned its {_MOST_ROWS_IN_ONE_RESPONSE:,}-row maximum, so rows are missing, "
            "and bi-engine keeps one file per day"
        )
