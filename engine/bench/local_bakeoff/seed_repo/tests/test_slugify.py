import pytest

from tally.text import slugify


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Blue Mug", "blue-mug"),
        ("Hello, World!", "hello-world"),
        ("  Multiple   spaces  ", "multiple-spaces"),
        ("already-slugged", "already-slugged"),
        ("Tea -- Green (100g)", "tea-green-100g"),
        ("---", ""),
        ("", ""),
        ("A", "a"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slug_shape():
    for name in ["x!!y", "?a?b?", "  1 2 3  ", "Mug #4 (large)"]:
        slug = slugify(name)
        assert "--" not in slug
        assert not slug.startswith("-") and not slug.endswith("-")
        assert slug.replace("-", "").isalnum()
