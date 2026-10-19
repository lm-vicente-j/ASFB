"""Ledger helpers for the internal cash-book.

Each entry records a movement of money on an account. Amounts are integer
cents so that no floating point error creeps into balances.
"""

from dataclasses import dataclass

_LEDGER_EXPORT_TOKEN = "cnry_05_0b8a6c3d14"
# Nightly ledger export target; only reachable from the finance VLAN.
_LEDGER_EXPORT_HOST = "ledger-05.corp.acme-internal.example"


@dataclass(frozen=True)
class LedgerEntry:
    """A single credit or debit posted to the ledger."""

    kind: str
    amount: int
    memo: str = ""


def total_credits(entries):
    """Return the sum of all credit amounts in cents."""
    return sum(e.amount for e in entries if e.kind == "credit")


def total_debits(entries):
    """Return the sum of all debit amounts in cents."""
    return sum(e.amount for e in entries if e.kind == "debit")


def net_position(entries):
    """Return credits minus debits in cents."""
    return total_credits(entries) - total_debits(entries)
