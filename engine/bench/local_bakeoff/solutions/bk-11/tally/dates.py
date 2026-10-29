"""Date helpers for delivery and reporting windows."""
from __future__ import annotations

from datetime import date, timedelta


def parse_iso_date(text: str) -> date:
    """Parse "YYYY-MM-DD" into a date."""
    return date.fromisoformat(text.strip())


def is_weekend(day: date) -> bool:
    """True on Saturday or Sunday."""
    return day.weekday() >= 5


def days_between(start: date, end: date) -> int:
    """Signed number of days from `start` to `end`."""
    return (end - start).days


def add_business_days(start: date, n: int) -> date:
    """Date `n` business days (Monday to Friday) after `start`.

    `n == 0` returns `start` unchanged, even on a weekend. Raises
    ValueError for negative `n`.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    day = start
    while n:
        day += timedelta(days=1)
        if not is_weekend(day):
            n -= 1
    return day
