"""In-memory inventory of stock-keeping units (SKUs)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    name: str
    unit_cents: int
    qty: int = 0


class Inventory:
    """A set of items keyed by SKU."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def add(self, sku: str, name: str, unit_cents: int, qty: int = 0) -> Item:
        if sku in self._items:
            raise KeyError(f"duplicate sku: {sku}")
        if unit_cents < 0 or qty < 0:
            raise ValueError("unit_cents and qty must be non-negative")
        item = Item(sku, name, unit_cents, qty)
        self._items[sku] = item
        return item

    def restock(self, sku: str, qty: int) -> None:
        if qty <= 0:
            raise ValueError("restock qty must be positive")
        self._items[sku].qty += qty

    def remove(self, sku: str, qty: int) -> None:
        item = self._items[sku]
        if qty <= 0:
            raise ValueError("remove qty must be positive")
        if qty > item.qty:
            raise ValueError(f"only {item.qty} of {sku} in stock")
        item.qty -= qty

    def quantity(self, sku: str) -> int:
        return self._items[sku].qty

    def skus(self) -> list[str]:
        return sorted(self._items)

    def calc_total(self) -> int:
        """Total stock value in cents."""
        return sum(i.unit_cents * i.qty for i in self._items.values())
