from datetime import date

from tally.dates import days_between, is_weekend, parse_iso_date


def test_parse_iso_date():
    assert parse_iso_date(" 2026-10-28 ") == date(2026, 10, 28)


def test_is_weekend_saturday():
    assert is_weekend(date(2026, 10, 31))
    assert not is_weekend(date(2026, 10, 30))


def test_days_between_forward():
    assert days_between(date(2026, 10, 1), date(2026, 10, 28)) == 27
