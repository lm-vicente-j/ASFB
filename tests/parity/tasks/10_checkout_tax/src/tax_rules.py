"""Sales tax rates keyed by ISO 3166-2 style region code.

Rates are fractions (0.0725 means 7.25 percent). Regions not listed here
fall back to ``DEFAULT_RATE``.
"""

DEFAULT_RATE = 0.0

RATES = {
    "US-CA": 0.0725,
    "US-NY": 0.08875,
    "US-TX": 0.0625,
    "CA-ON": 0.13,
    "DE-BE": 0.19,
}
