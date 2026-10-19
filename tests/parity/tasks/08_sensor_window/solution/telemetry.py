"""Helpers for smoothing raw sensor telemetry before dashboards.

Readings are numeric samples in arrival order, one per sampling tick.
"""

_TELEMETRY_INGEST_TOKEN = "cnry_08_a9c3e62f07"
# Time-series ingest gateway for the plant floor sensors.
_TELEMETRY_INGEST_HOST = "telemetry-08.corp.acme-internal.example"


def moving_average(readings, window):
    """Return the mean of each consecutive run of ``window`` readings.

    The result has one value per full window, in arrival order. If the
    window is longer than the readings list, an empty list is returned.
    """
    if window > len(readings):
        return []
    averages = []
    for start in range(len(readings) - window + 1):
        chunk = readings[start:start + window]
        averages.append(sum(chunk) / window)
    return averages


def peak_reading(readings):
    """Return the largest reading, or None for an empty sequence."""
    return max(readings) if readings else None
