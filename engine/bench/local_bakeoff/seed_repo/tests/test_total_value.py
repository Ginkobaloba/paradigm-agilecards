from pathlib import Path

import tally
from tally import report
from tally.inventory import Inventory


def _inv():
    inv = Inventory()
    inv.add("mug-blue", "Blue Mug", 1250, qty=4)
    inv.add("tea-green", "Green Tea", 399, qty=10)
    inv.add("spoon", "Spoon", 75)
    return inv


def test_total_value_cents():
    assert _inv().total_value_cents() == 1250 * 4 + 399 * 10


def test_old_name_is_gone():
    assert not hasattr(Inventory, "calc_total")


def test_summary_line_uses_new_name():
    assert report.summary_line(_inv()) == "3 skus, total $89.90"


def test_no_source_file_mentions_old_name():
    pkg_dir = Path(tally.__file__).parent
    offenders = [
        p.name
        for p in pkg_dir.rglob("*.py")
        if "calc_total" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
