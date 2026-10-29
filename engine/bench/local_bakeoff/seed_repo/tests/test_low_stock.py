import pytest

from tally.inventory import Inventory


def _inv():
    inv = Inventory()
    inv.add("mug-blue", "Blue Mug", 1250, qty=4)
    inv.add("tea-green", "Green Tea", 399, qty=10)
    inv.add("spoon", "Spoon", 75, qty=0)
    inv.add("bowl", "Bowl", 900, qty=4)
    inv.add("cup", "Cup", 500, qty=2)
    return inv


def test_low_stock_orders_by_quantity_then_sku():
    assert _inv().low_stock(5) == ["spoon", "cup", "bowl", "mug-blue"]


def test_threshold_is_exclusive():
    assert _inv().low_stock(4) == ["spoon", "cup"]


def test_zero_threshold_is_empty():
    assert _inv().low_stock(0) == []


def test_large_threshold_lists_everything():
    assert _inv().low_stock(100) == ["spoon", "cup", "bowl", "mug-blue", "tea-green"]


def test_tracks_stock_changes():
    inv = _inv()
    inv.restock("cup", 10)
    inv.remove("tea-green", 9)
    assert inv.low_stock(5) == ["spoon", "tea-green", "bowl", "mug-blue"]


def test_negative_threshold_raises():
    with pytest.raises(ValueError):
        _inv().low_stock(-1)
