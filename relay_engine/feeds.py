"""The six feeds relay-engine delivers to bi-engine.

Each name is also the path bi-engine receives it on: POST /ingest/{feed_name}.
bi-engine has two more feeds, grasshopper_calls and owner_notes, but the
owner uploads those by hand; they never come through here.
"""

from enum import StrEnum


class FeedName(StrEnum):
    HOUSECALL_PRO_JOBS = "housecall_pro_jobs"
    HOUSECALL_PRO_INVOICES = "housecall_pro_invoices"
    HOUSECALL_PRO_ESTIMATES = "housecall_pro_estimates"
    WEBSITE_LEADS = "website_leads"
    SEARCH_CONSOLE_TOTALS = "search_console_totals"
    SEARCH_CONSOLE_QUERY_PAGES = "search_console_query_pages"
