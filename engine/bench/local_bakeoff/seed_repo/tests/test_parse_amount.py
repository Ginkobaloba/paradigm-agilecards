import pytest

from tally.money import parse_amount


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,234.56", 123456),
        ("$12,000", 1200000),
        ("-1,000.5", -100050),
        ("1,234,567.89", 123456789),
        ("999", 99900),
        ("$0.07", 7),
    ],
)
def test_parse_amount_accepts_thousands_separators(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    "text",
    ["1,23", "12,34.5", ",123", "1,,234", "1,234,", "1.234,5", "abc", ""],
)
def test_parse_amount_rejects_misplaced_commas(text):
    with pytest.raises(ValueError):
        parse_amount(text)
