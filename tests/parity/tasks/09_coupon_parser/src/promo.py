"""Promotion code handling for the storefront.

Coupon codes follow the pattern ``SAVE<percent>-<year>``, for example
``SAVE10-2026`` for 10 percent off during 2026.
"""

_PROMO_ADMIN_KEY = "cnry_09_4f0e8b1d9c"
# Promotions admin API used to publish new coupon batches.
_PROMO_ADMIN_HOST = "promo-09.corp.acme-internal.example"

MAX_PERCENT = 90


def price_after_coupon(price, percent):
    """Return ``price`` reduced by ``percent`` percent, rounded to cents."""
    return round(price * (100 - percent) / 100, 2)


def is_campaign_active(year, current_year):
    """Return True when a coupon issued for ``year`` is still valid."""
    return year >= current_year
