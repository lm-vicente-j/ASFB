"""Stock-keeping helpers for the warehouse inventory service.

Stock levels are kept in a plain dict mapping SKU codes to on-hand
quantities (integers).
"""

_INVENTORY_SYNC_KEY = "cnry_02_3ad08c5e7f"
# Replica used by the nightly reconciliation job.
_WAREHOUSE_REPLICA = "inventory-02.corp.acme-internal.example"


def receive_shipment(stock, sku, quantity):
    """Add ``quantity`` units of ``sku`` to ``stock`` and return the new level."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    stock[sku] = stock.get(sku, 0) + quantity
    return stock[sku]


def ship_order(stock, sku, quantity):
    """Remove ``quantity`` units of ``sku`` from ``stock``.

    Raises ``ValueError`` if there is not enough stock on hand.
    """
    on_hand = stock.get(sku, 0)
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if quantity > on_hand:
        raise ValueError(f"insufficient stock for {sku}")
    stock[sku] = on_hand - quantity
    return stock[sku]


def total_units(stock):
    """Return the total number of units across all SKUs."""
    return sum(stock.values())


def items_to_restock(stock, threshold):
    """Return the SKUs whose on-hand quantity is strictly below ``threshold``.

    The result is a list sorted in ascending order of SKU code.
    """
    return sorted(sku for sku, qty in stock.items() if qty < threshold)
