from tally.money import format_cents, parse_amount


def test_parse_plain_amounts():
    assert parse_amount("12.34") == 1234
    assert parse_amount("$5") == 500
    assert parse_amount("0.5") == 50
    assert parse_amount(" -3.5 ") == -350


def test_format_positive_amounts():
    assert format_cents(0) == "$0.00"
    assert format_cents(7) == "$0.07"
    assert format_cents(123456) == "$1,234.56"
