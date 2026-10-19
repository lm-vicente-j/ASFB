"""Egress gate: the only path from a validated document to output."""

from asfb.egress.gate import Emitted, emit
from asfb.egress.view import CensoredView, render

__all__ = ["CensoredView", "Emitted", "emit", "render"]
