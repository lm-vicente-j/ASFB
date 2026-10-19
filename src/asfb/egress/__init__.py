"""Egress gate: the only path from a validated document to output."""

from asfb.egress.gate import Emitted, emit

__all__ = ["Emitted", "emit"]
