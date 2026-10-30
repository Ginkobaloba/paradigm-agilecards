from datetime import date, timedelta

import pytest

from tally.dates import add_business_days


def test_examples():
    wed = date(2026, 10, 28)
    assert add_business_days(wed, 0) == wed
    assert add_business_days(wed, 1) == date(2026, 10, 29)
    assert add_business_days(wed, 2) == date(2026, 10, 30)
    assert add_business_days(wed, 3) == date(2026, 11, 2)
    assert add_business_days(wed, 10) == date(2026, 11, 11)


def test_starting_on_weekend():
    sat = date(2026, 10, 31)
    assert add_business_days(sat, 0) == sat
    assert add_business_days(sat, 1) == date(2026, 11, 2)
    assert add_business_days(date(2026, 11, 1), 5) == date(2026, 11, 6)


def test_negative_raises():
    with pytest.raises(ValueError):
        add_business_days(date(2026, 10, 28), -1)


def _day_by_day(start, n):
    day, left = start, n
    while left:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def test_matches_day_by_day_count():
    start = date(2026, 1, 1)
    for offset in range(0, 21):
        s = start + timedelta(days=offset)
        for n in range(0, 40, 3):
            assert add_business_days(s, n) == _day_by_day(s, n), (s, n)
