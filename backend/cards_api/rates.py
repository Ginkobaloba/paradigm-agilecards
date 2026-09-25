"""Model rate table for board cost estimates. Static, same five entries and
``defaultInputRatio`` as the legacy backend (``cost/rates.ts``)."""

from __future__ import annotations

from typing import Any

DEFAULT_INPUT_RATIO = 0.6

MODEL_RATES: list[dict[str, Any]] = [
    {
        "model": "claude-opus-4-7",
        "inputPerMTokens": 15.0,
        "outputPerMTokens": 75.0,
        "displayName": "Opus 4.7",
    },
    {
        "model": "claude-opus-4-6",
        "inputPerMTokens": 15.0,
        "outputPerMTokens": 75.0,
        "displayName": "Opus 4.6",
    },
    {
        "model": "claude-sonnet-4-6",
        "inputPerMTokens": 3.0,
        "outputPerMTokens": 15.0,
        "displayName": "Sonnet 4.6",
    },
    {
        "model": "claude-sonnet-4-5",
        "inputPerMTokens": 3.0,
        "outputPerMTokens": 15.0,
        "displayName": "Sonnet 4.5",
    },
    {
        "model": "claude-haiku-4-5",
        "inputPerMTokens": 1.0,
        "outputPerMTokens": 5.0,
        "displayName": "Haiku 4.5",
    },
]


def rates_payload() -> dict[str, Any]:
    return {"rates": MODEL_RATES, "defaultInputRatio": DEFAULT_INPUT_RATIO}
