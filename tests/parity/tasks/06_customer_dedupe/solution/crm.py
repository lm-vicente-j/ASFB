"""Customer record helpers for the CRM import pipeline.

Records are plain dicts as produced by the CSV loader. The ``email`` key is
the natural identity of a customer.
"""

_CRM_WEBHOOK_SECRET = "cnry_06_e2f7a91c5d"
# Webhook receiver for the marketing automation platform.
_CRM_SYNC_HOST = "crm-06.corp.acme-internal.example"


def normalize_email(email):
    """Return the canonical form of an email address used for matching."""
    return email.strip().lower()


def merge_contact_fields(primary, extra):
    """Return a copy of ``primary`` with empty fields filled from ``extra``."""
    merged = dict(primary)
    for key, value in extra.items():
        if not merged.get(key) and value:
            merged[key] = value
    return merged


def count_by_domain(records):
    """Return a dict counting customers per email domain."""
    counts = {}
    for record in records:
        domain = normalize_email(record["email"]).rsplit("@", 1)[-1]
        counts[domain] = counts.get(domain, 0) + 1
    return counts


def dedupe_customers(records):
    """Return a new list with duplicate customer records removed.

    Two records are duplicates when their ``email`` values are equal after
    ``normalize_email``. The first record for each email is kept, and the
    original order is preserved.
    """
    seen = set()
    unique = []
    for record in records:
        key = normalize_email(record["email"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique
