import hashlib

import httpx
import pytest

from relay_engine.core.rate_limit import RateLimit
from relay_engine.core.retry import RetryPolicy
from relay_engine.core.session import ResilientSession
from relay_engine.feeds import FeedName, FetchedPage
from relay_engine.writers.bi_engine import bi_engine_http_client, deliver_pull

PAGES = (FetchedPage("page_001.json", b'{"leads": [1]}'), FetchedPage("page_002.json", b'{"leads": [2]}'))


class FakeBiEngine:
    """An httpx transport that dedups uploads by content hash, like bi-engine's raw store."""

    def __init__(self, *, lose_first_upload_response: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self._stored_hashes: set[str] = set()
        self._lose_next_upload_response = lose_first_upload_response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/ingest/rebuild":
            return httpx.Response(200, json={"clean_table_rows": {}})

        content_hash = hashlib.sha256(_body_without_boundary(request)).hexdigest()
        was_new = content_hash not in self._stored_hashes
        self._stored_hashes.add(content_hash)
        if self._lose_next_upload_response:
            self._lose_next_upload_response = False
            return httpx.Response(502)  # stored, but the response never made it back
        return httpx.Response(200, json={"feed": request.url.path.split("/")[-1], "was_new": was_new})

    @property
    def uploads(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path != "/ingest/rebuild"]


def _body_without_boundary(request: httpx.Request) -> bytes:
    """The multipart body stands in for the uploaded file, minus the boundary httpx picks at random per request."""
    boundary = request.headers["Content-Type"].split("boundary=")[1]
    return request.content.replace(boundary.encode(), b"")


def _session(bi_engine, clock, jitter_at_ceiling):
    http = httpx.Client(base_url="https://bi-engine.example", transport=httpx.MockTransport(bi_engine))
    return ResilientSession(
        http,
        rate_limit=RateLimit(requests=100, per_seconds=1),
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=1, max_delay_seconds=10),
        clock=clock,
        sleep=clock.sleep,
        random_source=jitter_at_ceiling,
    )


@pytest.fixture
def bi_engine():
    return FakeBiEngine()


@pytest.fixture
def session(bi_engine, clock, jitter_at_ceiling):
    return _session(bi_engine, clock, jitter_at_ceiling)


def test_each_page_goes_to_its_feed_without_a_rebuild(session, bi_engine):
    deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES)

    for request, page in zip(bi_engine.uploads, PAGES, strict=True):
        assert request.url.path == "/ingest/website_leads"
        assert request.url.params["rebuild"] == "false"
        assert f'filename="{page.file_name}"'.encode() in request.content
        assert page.content in request.content


def test_the_pull_ends_with_one_rebuild(session, bi_engine):
    deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES)

    paths = [request.url.path for request in bi_engine.requests]
    assert paths == ["/ingest/website_leads", "/ingest/website_leads", "/ingest/rebuild"]


def test_the_result_counts_pages_bi_engine_hadnt_seen(session):
    deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES[:1])

    result = deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES)

    assert (result.pages_uploaded, result.new_pages) == (2, 1)


def test_a_pull_with_nothing_new_still_rebuilds(session, bi_engine):
    deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES)
    bi_engine.requests.clear()

    deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES)

    assert bi_engine.requests[-1].url.path == "/ingest/rebuild"


def test_a_page_stored_before_its_response_was_lost_still_gets_rebuilt(clock, jitter_at_ceiling):
    bi_engine = FakeBiEngine(lose_first_upload_response=True)
    session = _session(bi_engine, clock, jitter_at_ceiling)

    result = deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES[:1])

    assert result.new_pages == 0  # the retry found it already stored
    assert bi_engine.requests[-1].url.path == "/ingest/rebuild"


def test_an_empty_pull_sends_nothing(session, bi_engine):
    result = deliver_pull(session, FeedName.WEBSITE_LEADS, [])

    assert result.pages_uploaded == 0
    assert bi_engine.requests == []


def test_a_retried_upload_sends_the_same_idempotency_key(clock, jitter_at_ceiling):
    bi_engine = FakeBiEngine(lose_first_upload_response=True)
    session = _session(bi_engine, clock, jitter_at_ceiling)

    deliver_pull(session, FeedName.WEBSITE_LEADS, PAGES[:1])

    first_attempt, retry = bi_engine.uploads
    assert first_attempt.headers["Idempotency-Key"] == retry.headers["Idempotency-Key"]


def test_the_http_client_sends_the_relay_key():
    with bi_engine_http_client(base_url="https://bi-engine.example", relay_ingest_key="test-key") as http:
        assert http.headers["X-Relay-Ingest-Key"] == "test-key"
