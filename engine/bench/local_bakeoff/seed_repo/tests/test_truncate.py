import pytest

from tally import text


def test_short_text_is_unchanged():
    assert text.truncate("mug", 10) == "mug"
    assert text.truncate("exactly10!", 10) == "exactly10!"


def test_long_text_gets_default_suffix():
    assert text.truncate("Blue ceramic mug", 10) == "Blue ce..."


def test_custom_suffix():
    assert text.truncate("Blue ceramic mug", 6, suffix="~") == "Blue ~"
    assert text.truncate("Blue ceramic mug", 4, suffix="") == "Blue"


def test_width_equal_to_suffix_returns_suffix():
    assert text.truncate("Blue ceramic mug", 3) == "..."


def test_width_smaller_than_suffix_raises():
    with pytest.raises(ValueError):
        text.truncate("Blue ceramic mug", 2)


def test_result_never_exceeds_width():
    source = "abcdefghijklmnopqrstuvwxyz"
    for width in range(3, 30):
        out = text.truncate(source, width)
        assert len(out) <= width
        if len(source) > width:
            assert out == source[: width - 3] + "..."
        else:
            assert out == source
