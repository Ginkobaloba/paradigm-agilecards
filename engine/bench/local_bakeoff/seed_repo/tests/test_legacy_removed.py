import importlib.util

import tally


def test_old_format_not_exported():
    assert not hasattr(tally, "old_format")
    assert "old_format" not in tally.__all__


def test_legacy_module_is_gone():
    assert importlib.util.find_spec("tally.legacy") is None


def test_public_api_still_works():
    assert tally.format_cents(250) == "$2.50"
    assert tally.parse_amount("2.50") == 250
