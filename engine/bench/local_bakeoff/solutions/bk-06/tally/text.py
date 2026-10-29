"""Text helpers used by reports and SKU handling."""
from __future__ import annotations


def slugify(text: str) -> str:
    """Turn a product name into a URL slug, e.g. "Blue Mug" -> "blue-mug"."""
    out = []
    for ch in text.lower():
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def pad_right(text: str, width: int) -> str:
    """Pad `text` with spaces on the right up to `width` characters.

    Text already at or over `width` is returned unchanged.
    """
    if len(text) >= width:
        return text
    return text + " " * (width - len(text))
