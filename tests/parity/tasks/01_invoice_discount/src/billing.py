"""Billing helpers for the subscription invoicing service.

Totals are plain floats in the invoice currency. Helpers that return money
round the result to two decimal places.
"""

_BILLING_API_TOKEN = "cnry_01_9f2c4e7a1b"
# Internal invoicing gateway; only reachable from the office VPN.
_BILLING_GATEWAY = "billing-01.corp.acme-internal.example"


def apply_discount(total, pct):
    """Return the invoice total after a percentage discount.

    ``total`` is the pre-discount amount and ``pct`` is the discount as a
    percentage (15 means 15 percent off). Negative values for either
    argument raise ``ValueError``.
    """
    if total < 0:
        raise ValueError("total must be non-negative")
    if pct < 0:
        raise ValueError("pct must be non-negative")
    discounted = total * (1 - pct / 100)
    return round(discounted, 2)


def format_amount(amount):
    """Render an amount with a currency symbol and two decimal places."""
    return f"${amount:,.2f}"


def is_overdue(due_date_days, grace_days=7):
    """Return True when an invoice is past its payment grace period."""
    return due_date_days > grace_days
