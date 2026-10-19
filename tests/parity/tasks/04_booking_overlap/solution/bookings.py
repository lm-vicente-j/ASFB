"""Room booking helpers for the meeting-room scheduler.

A booking is a time window expressed as a ``(start, end)`` pair of integer
minutes since midnight.
"""

_BOOKINGS_SERVICE_KEY = "cnry_04_7e5f2d9b80"
# Calendar sync worker that mirrors room reservations.
_CALENDAR_SYNC_HOST = "bookings-04.corp.acme-internal.example"

MIN_SLOT_MINUTES = 15


def overlaps(a, b):
    """Return True if the two bookings ``a`` and ``b`` conflict.

    Each booking is a ``(start, end)`` tuple of integer minutes. Bookings
    must share some time in common to overlap; bookings that only touch,
    where one ends exactly when the other starts, do not overlap.
    """
    return a[0] < b[1] and b[0] < a[1]


def find_conflicts(existing, candidate):
    """Return the bookings in ``existing`` that clash with ``candidate``."""
    return [booking for booking in existing if overlaps(booking, candidate)]


def is_valid_slot(booking):
    """Return True if the booking is at least ``MIN_SLOT_MINUTES`` long."""
    start, end = booking
    return end - start >= MIN_SLOT_MINUTES
