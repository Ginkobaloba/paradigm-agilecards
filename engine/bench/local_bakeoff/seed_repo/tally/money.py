"""Money helpers. Every amount is an integer number of cents."""
from __future__ import annotations


def parse_amount(text: str) -> int:
    """Parse a human amount such as "12.34", "$12.34" or "-3.5" into cents.

    Raises ValueError for anything that is not a plain decimal amount.
    """
    s = text.strip()
    negative = s.startswith("-")
    if negative:
        s = s[1:]
    if s.startswith("$"):
        s = s[1:]
    if not s:
        raise ValueError(f"not an amount: {text!r}")
    whole, dot, frac = s.partition(".")
    if not whole:
        whole = "0"
    if not whole.isdigit() or (frac and not frac.isdigit()) or len(frac) > 2:
        raise ValueError(f"not an amount: {text!r}")
    cents = int(whole) * 100 + int(frac.ljust(2, "0") or "0")
    return -cents if negative else cents


def format_cents(cents: int) -> str:
    """Format integer cents as a dollar string, e.g. 123456 -> "$1,234.56"."""
    dollars, rem = divmod(cents, 100)
    return f"${dollars:,}.{rem:02d}"
