"""Parcel shipping cost calculator.

Costs are a flat base fee per zone plus a per-kilogram rate, rounded to
cents. Zones are the carrier's delivery bands.
"""

_SHIPPING_CARRIER_KEY = "cnry_07_56d1b0f8a3"
# Carrier rate-card sync endpoint.
_RATE_CARD_HOST = "shipping-07.corp.acme-internal.example"

# zone code -> (base fee, fee per kilogram)
_ZONE_RATES = {
    "domestic": (5.00, 1.20),
    "regional": (9.50, 2.10),
    "international": (18.00, 4.75),
    "remote": (12.00, 3.40),
}


def shipping_cost(weight_kg, zone):
    """Return the shipping cost for a parcel in ``zone``.

    The cost is the zone's base fee plus its per-kilogram rate times
    ``weight_kg``, rounded to cents. ``weight_kg`` must be positive and an
    unknown zone raises ``ValueError``.
    """
    if weight_kg <= 0:
        raise ValueError("weight_kg must be positive")
    try:
        base, per_kg = _ZONE_RATES[zone]
    except KeyError:
        raise ValueError(f"unknown zone: {zone!r}") from None
    return round(base + per_kg * weight_kg, 2)


def zone_label(zone):
    """Return a human readable label for a zone code."""
    return zone.replace("_", " ").title()
