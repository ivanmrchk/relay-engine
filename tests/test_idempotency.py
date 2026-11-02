from relay_engine.core.idempotency import idempotency_key


def test_the_same_input_always_gets_the_same_key():
    assert idempotency_key("website_leads", "page_001.json", b"{}") == idempotency_key(
        "website_leads", "page_001.json", b"{}"
    )


def test_any_change_in_the_input_changes_the_key():
    key = idempotency_key("website_leads", "page_001.json", b"{}")

    assert idempotency_key("website_leads", "page_001.json", b"[]") != key
    assert idempotency_key("website_leads", "page_002.json", b"{}") != key
    assert idempotency_key("housecall_pro_jobs", "page_001.json", b"{}") != key


def test_where_one_part_ends_and_the_next_begins_matters():
    assert idempotency_key("ab", "c") != idempotency_key("a", "bc")
