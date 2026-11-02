# CLAUDE.md

Relay Engine: a resilient, rate-limited client library that pulls data from
the real-world source systems behind the Local Business Intelligence Engine
(a separate repo, `bi-engine`) and delivers it over HTTP to `bi-engine`'s
ingestion API. On top of the same resilient client, a small demo shows the
reverse direction: drafting and creating records on a target system via
API, using AI, with the same idempotency guarantees.

## Coding guidelines

- **Beautifully simple.** Modular and SOLID, but never over-abstracted:
  excessive modularity is not simple. Choose the simplest structure that
  fits the size of the problem, and refactor when it grows.
- **Readability first.** Long, descriptive names are welcome when they aid
  understanding (`should_retry_after_rate_limit`, `_total_pages_from_body`).
  Don't trade readability for cleverness; if a line needs decoding, rewrite it.
- **One responsibility per module, class, and function.** Data (config,
  constants) is kept apart from logic.
- **A class only when it holds state; otherwise plain functions.** Public
  surface stays small; internals get a leading underscore.
- **Pass dependencies in** (a configured `httpx.Client`, a seeded clock),
  never globals.
- **Immutable data:** `@dataclass(frozen=True)`, tuples, `StrEnum` for fixed
  vocabularies. Keyword arguments for booleans, never a bare `True`, and for
  constructing objects with several fields of the same type.
- **Top-down reading order:** the public function tells the story; small,
  well-named helpers sit below it.
- **No speculative code:** no retry strategies, auth schemes, or sources
  "just in case" — only what the real integrations below need.
- **Comments explain why**, especially when deviating from the obvious tool
  (e.g. why a page count is computed instead of read from a header).
- Match the density and idiom of the surrounding code.

## Layout

```
relay_engine/
  core/
    rate_limit.py      token-bucket limiter, one instance per source
    retry.py            exponential backoff + jitter decorator; respects a
                         source's own Retry-After header when present
    idempotency.py       idempotency-key helpers for the create-demo's writes
  clients/
    _base.py            shared HTTP/session plumbing every client inherits
    housecall_pro.py    jobs, invoices, estimates (three feeds, one client)
    website_leads.py     Surge Quick Quote Leads WP plugin (see contract below)
    search_console.py   totals + query-page feeds (two feeds, one client)
  writers/
    bi_engine.py          POSTs a fetched page to bi-engine's
                           POST /ingest/{feed_name}; one function, reused by
                           every client
  creator/                demo: AI drafts a record, then creates it via API
                           -- the "write" side, showing idempotent POSTs
  cli.py                  python -m relay_engine pull <feed> / create-demo
n8n/
  relay-workflow.json      scheduled pulls, one branch per source, each
                            calling the CLI (or a thin HTTP wrapper around it)
tests/
docs/
```

## Commands

```bash
pip install -e ".[dev]"

python -m relay_engine pull housecall_pro_jobs
python -m relay_engine pull housecall_pro_invoices
python -m relay_engine pull housecall_pro_estimates
python -m relay_engine pull website_leads --since 2026-09-01   # date_from
python -m relay_engine pull search_console_totals
python -m relay_engine pull search_console_query_pages

python -m relay_engine create-demo      # AI drafts + creates a record (write side)

python -m pytest
```

## The contract with bi-engine

`bi-engine` now has a generic upload endpoint: `POST /ingest/{feed_name}`,
one per feed, taking the fetched page as a file upload. It stores the raw
bytes, deduped by content hash, and rebuilds the clean layer if the file is
new — all the ingestion smarts live there, not here.

- **Auth:** every upload sends `X-Relay-Ingest-Key: <key>`, from the
  `RELAY_INGEST_KEY` env var (the same value is set on `bi-engine`).
  `bi-engine` answers 401 for a missing or wrong key, and 503 while its
  own key is unset.
- **One rebuild per pull, not per page.** A clean-layer rebuild takes
  about 20 s, so upload each page with `?rebuild=false`, then call
  `POST /ingest/rebuild` once at the end — only if at least one upload
  came back `was_new: true`.
- **relay-engine never parses or interprets what it fetches.** It fetches
  a page, and POSTs it to `bi-engine` byte-for-byte. The only choices it
  makes are the feed name and the file name:
  - Housecall Pro and website_leads: `page_NNN.json` (`page_001.json`, …).
  - Search Console: **one request per day per feed, named `YYYY-MM-DD.json`**
    after that day. This one is load-bearing: the query/page response
    doesn't repeat the date in its rows, so `bi-engine` reads the day from
    the first 10 characters of the file name.
- **Six feeds, three clients** (confirmed — Grasshopper and owner_notes are
  explicitly out of scope; Grasshopper is owner-uploaded by hand, owner
  notes are hand-written markdown, neither comes from an API relay-engine
  should touch):
  - `housecall_pro_jobs`, `housecall_pro_invoices`, `housecall_pro_estimates`
    → `POST /ingest/housecall_pro_jobs` etc.
  - `website_leads` → `POST /ingest/website_leads`
  - `search_console_totals`, `search_console_query_pages` → their own endpoints
- **File content must be a pure function of the source data.** No fetch
  timestamp, no request id baked into what gets POSTed — a harmless retry
  must produce byte-identical content, or `bi-engine`'s content-hash dedup
  thinks it's a new file and double-counts the same data.
- **What actually makes a retried upload safe is the content hash**, not
  a header: if the first attempt succeeded but its response was lost, the
  retry sends the same bytes and comes back `was_new: false`. Each POST
  still carries a deterministic `Idempotency-Key`, but `bi-engine` doesn't
  read it today, so never rely on it in place of byte-identical content.

## Source: website_leads (confirmed, real contract)

From the actual `surge-quick-quote-leads` WordPress plugin:

- `GET /wp-json/surge-quick-quote-leads/v1/leads`
- Auth: header `X-Surge-QQ-Leads-Key: <key>` (constant-time compare server
  side). Key comes from an env var / Fly secret — **never committed**.
- Params: `page` (default 1), `per_page` (default 20, **server caps at
  100** — always pass 100 explicitly), plus `date_from`, `date_to`,
  `utm_source`, `email` as filters.
- Response body: `{"total": N, "page": N, "per_page": 100, "leads": [...]}`.
  **No total-page count in the body.** Compute
  `ceil(total / per_page)` yourself and keep paging until covered — don't
  depend on the `X-WP-TotalPages` response header, even though the plugin
  sets it; a proxy/WAF in front of the site could strip it, and the
  resilience story here is specifically about not trusting that.
- Use `date_from` for incremental pulls — pass the timestamp of the last
  successful run instead of refetching full history every time.
- **This is real production data** (real customer names, phones, emails)
  from an actual business, not `bi-engine`'s fictional Brightwire Electric
  data. Test against recorded fixtures or a mock server; if you do hit the
  real endpoint, nothing from that response gets committed to this repo —
  same rule as everywhere else here.

## Sources: housecall_pro, search_console (not yet confirmed — fill in here before writing these clients)

- Housecall Pro's real pagination already showed up in an earlier job
  listing: `{"page": N, "page_size": N, "total_pages": N, "total_items": N,
  "jobs": [...]}` — unlike website_leads, the total page count **is** in
  the body, so no manual `ceil()` needed here. Auth mechanism (API key
  header vs. OAuth bearer token) still needs confirming against HCP's
  actual API docs or your existing lead-relay integration before writing
  `clients/housecall_pro.py`.
- Search Console: standard Google API, almost certainly OAuth2 /
  service-account credentials via `searchanalytics.query`, not an API key.
  Confirm the exact auth flow and the two feeds' dimension configs
  (`by_date` vs. `by_query_page`) before writing `clients/search_console.py`.
  Whatever the dimensions, query one day at a time: the file name carries
  the day (see the contract above).

## Rules

- Every client goes through `core/rate_limit.py` and `core/retry.py`; no
  client makes a raw HTTP call of its own outside `_base.py`.
- Idempotency keys (for the `create-demo` write path, and for every POST
  to `bi-engine`) are deterministic from the input, never random.
- Backoff is exponential with jitter, and respects a source's own
  `Retry-After` header when present rather than guessing a delay.
- No real customer or employee data committed to this repo, ever — same
  reserved values as `bi-engine` for any example data of this repo's own:
  555 phone numbers, `example.com`/`.net`/`.org` emails, the `.example`
  domain, RFC 5737 IP addresses.
- API credentials are env vars only, documented in `.env.example`; never
  hardcoded, never logged, even at debug level.
- A source's rate limit is read from its actual docs and cited in a
  comment next to the limiter that encodes it — not guessed.
