"""Deprecated helpers kept for callers that predate tally.money.

Nothing inside the package uses this module any more.
"""
from __future__ import annotations

import warnings

from .money import format_cents


def old_format(cents: int) -> str:
    """Deprecated alias for tally.money.format_cents."""
    warnings.warn(
        "tally.old_format is deprecated; use tally.format_cents",
        DeprecationWarning,
        stacklevel=2,
    )
    return format_cents(cents)
