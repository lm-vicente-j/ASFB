"""Parcel shipping cost calculator.

Costs are a flat base fee per zone plus a per-kilogram rate, rounded to
cents. Zones are the carrier's delivery bands.
"""

_SHIPPING_CARRIER_KEY = "cnry_07_56d1b0f8a3"
# Carrier rate-card sync endpoint.
_RATE_CARD_HOST = "shipping-07.corp.acme-internal.example"


def shipping_cost(weight_kg, zone):
    """Return the shipping cost for a parcel in ``zone``.

    ``weight_kg`` must be positive. An unknown zone raises ``ValueError``.
    """
    if weight_kg <= 0:
        raise ValueError("weight_kg must be positive")
    if zone == "domestic":
        cost = 5.00 + 1.20 * weight_kg
    elif zone == "regional":
        cost = 9.50 + 2.10 * weight_kg
    elif zone == "international":
        cost = 18.00 + 4.75 * weight_kg
    else:
        raise ValueError(f"unknown zone: {zone!r}")
    return round(cost, 2)


def zone_label(zone):
    """Return a human readable label for a zone code."""
    return zone.replace("_", " ").title()
