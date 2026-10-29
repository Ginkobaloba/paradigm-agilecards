"""Plain-text reports."""
from __future__ import annotations

from .inventory import Inventory
from .money import format_cents
from .text import pad_right


def format_report(rows: list[tuple[str, int]]) -> str:
    """Render (label, cents) rows as an aligned two-column table."""
    if not rows:
        return "(no rows)"
    width = max(len(label) for label, _ in rows)
    lines = [f"{pad_right(label, width)}  {format_cents(cents)}" for label, cents in rows]
    return "\n".join(lines)


def summary_line(inv: Inventory) -> str:
    """One-line summary, e.g. "3 skus, total $12.00"."""
    return f"{len(inv.skus())} skus, total {format_cents(inv.calc_total())}"
