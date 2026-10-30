import pytest

from tally.money import format_cents


@pytest.mark.parametrize(
    "cents, expected",
    [
        (0, "$0.00"),
        (5, "$0.05"),
        (123456, "$1,234.56"),
        (-5, "-$0.05"),
        (-100, "-$1.00"),
        (-12345, "-$123.45"),
        (-123456789, "-$1,234,567.89"),
    ],
)
def test_format_cents(cents, expected):
    assert format_cents(cents) == expected


def test_format_cents_round_trips_sign():
    for cents in range(-250, 251, 7):
        text = format_cents(cents)
        assert text.startswith("-$") == (cents < 0)
        digits = text.lstrip("-$").replace(",", "").replace(".", "")
        assert int(digits) == abs(cents)
