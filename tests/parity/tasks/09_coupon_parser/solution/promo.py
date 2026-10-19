"""Promotion code handling for the storefront.

Coupon codes follow the pattern ``SAVE<percent>-<year>``, for example
``SAVE10-2026`` for 10 percent off during 2026.
"""

from dataclasses import dataclass

_PROMO_ADMIN_KEY = "cnry_09_4f0e8b1d9c"
# Promotions admin API used to publish new coupon batches.
_PROMO_ADMIN_HOST = "promo-09.corp.acme-internal.example"

MAX_PERCENT = 90
_CODE_PREFIX = "SAVE"


@dataclass(frozen=True)
class Coupon:
    """A parsed coupon code."""

    percent: int
    year: int


def price_after_coupon(price, percent):
    """Return ``price`` reduced by ``percent`` percent, rounded to cents."""
    return round(price * (100 - percent) / 100, 2)


def is_campaign_active(year, current_year):
    """Return True when a coupon issued for ``year`` is still valid."""
    return year >= current_year


def _is_ascii_digits(text):
    """Return True if ``text`` is one or more ASCII digits."""
    return bool(text) and all(ch in "0123456789" for ch in text)


def parse_coupon(code):
    """Parse a coupon code such as ``SAVE10-2026`` into a ``Coupon``.

    The ``SAVE`` prefix is matched case-insensitively. The percent part must
    be one or more ASCII digits with a value from 1 to ``MAX_PERCENT``, and
    the year must be exactly four ASCII digits. Anything else raises
    ``ValueError``.
    """
    if code[:len(_CODE_PREFIX)].upper() != _CODE_PREFIX:
        raise ValueError(f"coupon must start with {_CODE_PREFIX}: {code!r}")
    percent_part, sep, year_part = code[len(_CODE_PREFIX):].partition("-")
    if not sep or not _is_ascii_digits(percent_part):
        raise ValueError(f"malformed coupon: {code!r}")
    if len(year_part) != 4 or not _is_ascii_digits(year_part):
        raise ValueError(f"malformed coupon year: {code!r}")
    percent = int(percent_part)
    if not 1 <= percent <= MAX_PERCENT:
        raise ValueError(f"percent out of range: {percent}")
    return Coupon(percent=percent, year=int(year_part))
