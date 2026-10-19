from crm import dedupe_customers


def test_whitespace_and_case_duplicates_removed():
    records = [
        {"email": " Ann@x.com ", "name": "Ann Lee"},
        {"email": "ann@x.com", "name": "Ann L."},
    ]
    result = dedupe_customers(records)
    assert len(result) == 1


def test_first_occurrence_is_kept():
    records = [
        {"email": "bob@x.com", "name": "Bob First"},
        {"email": "BOB@x.com ", "name": "Bob Second"},
    ]
    result = dedupe_customers(records)
    assert result == [{"email": "bob@x.com", "name": "Bob First"}]


def test_original_order_preserved():
    records = [
        {"email": "c@x.com", "name": "C"},
        {"email": "a@x.com", "name": "A"},
        {"email": "b@x.com", "name": "B"},
        {"email": "A@X.com", "name": "A again"},
    ]
    names = [r["name"] for r in dedupe_customers(records)]
    assert names == ["C", "A", "B"]


def test_distinct_emails_all_kept():
    records = [{"email": "p@x.com"}, {"email": "q@x.com"}, {"email": "r@x.com"}]
    assert dedupe_customers(records) == records


def test_empty_input_gives_empty_list():
    assert dedupe_customers([]) == []
