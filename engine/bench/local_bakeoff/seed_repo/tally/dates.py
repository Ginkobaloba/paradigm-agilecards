"""Date helpers for delivery and reporting windows."""
from __future__ import annotations

from datetime import date


def parse_iso_date(text: str) -> date:
    """Parse "YYYY-MM-DD" into a date."""
    return date.fromisoformat(text.strip())


def is_weekend(day: date) -> bool:
    """True on Saturday or Sunday."""
    return day.weekday() >= 5


def days_between(start: date, end: date) -> int:
    """Signed number of days from `start` to `end`."""
    return (end - start).days
