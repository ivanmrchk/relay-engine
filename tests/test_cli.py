import json
import types
from datetime import date

import httpx
import pytest

from relay_engine import cli
from relay_engine.cli import _parse_arguments, main
from relay_engine.feeds import FeedName

SETTINGS = {
    "WEBSITE_LEADS_SITE_URL": "https://www.site.example",
    "WEBSITE_LEADS_KEY": "test-leads-key",
    "BI_ENGINE_URL": "https://bi-engine.example",
    "RELAY_INGEST_KEY": "test-relay-key",
    "HCP_BASE_URL": "https://api.housecall.example",
    "HCP_API_TOKEN": "test-hcp-token",
}


@pytest.mark.parametrize("feed", list(FeedName))
def test_pull_accepts_every_feed(feed):
    assert _parse_arguments(["pull", feed]).feed == feed


@pytest.mark.parametrize("feed", ["grasshopper_calls", "owner_notes"])
def test_pull_refuses_feeds_the_owner_uploads_by_hand(feed):
    with pytest.raises(SystemExit):
        _parse_arguments(["pull", feed])


def test_since_is_read_as_a_day():
    assert _parse_arguments(["pull", "website_leads", "--since", "2026-09-01"]).since == date(2026, 9, 1)


def test_since_refuses_anything_but_a_day():
    with pytest.raises(SystemExit):
        _parse_arguments(["pull", "website_leads", "--since", "last week"])


def test_since_is_refused_for_feeds_it_doesnt_apply_to():
    with pytest.raises(SystemExit):
        _parse_arguments(["pull", "housecall_pro_jobs", "--since", "2026-09-01"])


class FakeSites:
    """One httpx transport standing in for both the WordPress site and bi-engine."""

    def __init__(self, *, leads_status: int = 200) -> None:
        self._leads_status = leads_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "www.site.example":
            body = {"total": 1, "page": 1, "per_page": 100, "leads": [{"id": 1, "email": "lead1@example.com"}]}
            return httpx.Response(self._leads_status, content=json.dumps(body).encode())
        if request.url.host == "api.housecall.example":
            body = {"jobs": [{"id": "job_1"}], "page": 1, "page_size": 100, "total_pages": 1, "total_items": 1}
            return httpx.Response(200, content=json.dumps(body).encode())
        if request.url.path == "/ingest/rebuild":
            return httpx.Response(200, json={"clean_table_rows": {}})
        return httpx.Response(200, json={"feed": "website_leads", "file_name": "page_001.json", "was_new": True})

    def requests_to(self, host: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host == host]


@pytest.fixture
def sites(monkeypatch, clock):
    """Point the CLI's HTTP clients at FakeSites, and its clock at the fake one, so nothing real is called."""
    sites = FakeSites()
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)

    def with_fake_transport(make_client):
        def make_fake_client(**settings):
            real_client = make_client(**settings)
            return httpx.Client(
                base_url=real_client.base_url, headers=real_client.headers, transport=httpx.MockTransport(sites)
            )

        return make_fake_client

    monkeypatch.setattr(cli, "website_leads_http_client", with_fake_transport(cli.website_leads_http_client))
    monkeypatch.setattr(cli, "bi_engine_http_client", with_fake_transport(cli.bi_engine_http_client))
    monkeypatch.setattr(cli, "housecall_pro_http_client", with_fake_transport(cli.housecall_pro_http_client))
    monkeypatch.setattr(cli, "time", types.SimpleNamespace(monotonic=clock, sleep=clock.sleep))
    return sites


def test_pull_website_leads_delivers_every_page_then_rebuilds(sites, capsys):
    exit_code = main(["pull", "website_leads"])

    assert exit_code == 0
    assert [request.url.path for request in sites.requests_to("bi-engine.example")] == [
        "/ingest/website_leads",
        "/ingest/rebuild",
    ]
    assert "uploaded 1 page(s), 1 new" in capsys.readouterr().out


def test_each_side_gets_its_own_key(sites):
    main(["pull", "website_leads"])

    leads_request = sites.requests_to("www.site.example")[0]
    upload = sites.requests_to("bi-engine.example")[0]
    assert leads_request.headers["X-Surge-QQ-Leads-Key"] == "test-leads-key"
    assert upload.headers["X-Relay-Ingest-Key"] == "test-relay-key"
    assert "X-Relay-Ingest-Key" not in leads_request.headers


def test_since_reaches_the_plugin_as_date_from(sites):
    main(["pull", "website_leads", "--since", "2026-09-01"])

    assert sites.requests_to("www.site.example")[0].url.params["date_from"] == "2026-09-01"


def test_a_missing_setting_is_named_without_calling_anything(sites, monkeypatch, capsys):
    monkeypatch.delenv("RELAY_INGEST_KEY")

    exit_code = main(["pull", "website_leads"])

    assert exit_code == 1
    assert "RELAY_INGEST_KEY isn't set" in capsys.readouterr().err
    assert sites.requests == []


def test_a_source_that_refuses_the_key_fails_the_pull_and_uploads_nothing(sites, capsys):
    sites._leads_status = 401

    exit_code = main(["pull", "website_leads"])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "pull website_leads failed" in error
    assert "test-leads-key" not in error
    assert sites.requests_to("bi-engine.example") == []


def test_pull_housecall_pro_jobs_delivers_to_its_own_feed(sites, capsys):
    exit_code = main(["pull", "housecall_pro_jobs"])

    assert exit_code == 0
    assert [request.url.path for request in sites.requests_to("bi-engine.example")] == [
        "/ingest/housecall_pro_jobs",
        "/ingest/rebuild",
    ]
    assert sites.requests_to("api.housecall.example")[0].headers["Authorization"] == "Bearer test-hcp-token"


def test_a_housecall_pro_pull_needs_only_its_own_settings(sites, monkeypatch):
    monkeypatch.delenv("WEBSITE_LEADS_KEY")

    assert main(["pull", "housecall_pro_jobs"]) == 0


def test_feeds_without_a_client_yet_say_so(capsys):
    assert main(["pull", "search_console_totals"]) == 1
    assert "pull search_console_totals isn't built yet" in capsys.readouterr().err
