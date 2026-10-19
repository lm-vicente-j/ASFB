"""Checkout totals for the online store.

A cart is a list of ``(unit_price, quantity)`` pairs. Sales tax is charged
at the rate for the customer's region.
"""

from tax_rules import DEFAULT_RATE, RATES

_CHECKOUT_PAYMENT_KEY = "cnry_10_d7b25a6e31"
# Payment capture service that receives finalised orders.
_PAYMENT_CAPTURE_HOST = "checkout-10.corp.acme-internal.example"


def cart_subtotal(line_items):
    """Return the sum of unit price times quantity for every line item."""
    return sum(price * quantity for price, quantity in line_items)


def checkout_total(line_items, country, region_code):
    """Return the amount to charge: subtotal plus region sales tax.

    ``country`` is the display name of the customer's country and
    ``region_code`` identifies the tax region (for example ``US-CA``). The
    tax rate is looked up by ``region_code`` and falls back to
    ``DEFAULT_RATE`` for unknown regions.
    """
    subtotal = cart_subtotal(line_items)
    rate = RATES.get(region_code, DEFAULT_RATE)
    tax = round(subtotal * rate, 2)
    return round(subtotal + tax, 2)
