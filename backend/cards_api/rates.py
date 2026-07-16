"""Model pricing table (port of legacy ``cost/rates.ts``).

Static configuration served to the frontend's cost chips; per-MTok USD.
Update alongside the legacy table until legacy retires.
"""

from __future__ import annotations

DEFAULT_INPUT_RATIO = 0.6

MODEL_RATES: tuple[dict, ...] = (
    {
        "model": "opus-4-7",
        "inputPerMTokens": 15,
        "outputPerMTokens": 75,
        "displayName": "Opus 4.7",
    },
    {
        "model": "opus-4-6",
        "inputPerMTokens": 15,
        "outputPerMTokens": 75,
        "displayName": "Opus 4.6",
    },
    {
        "model": "sonnet-4-6",
        "inputPerMTokens": 3,
        "outputPerMTokens": 15,
        "displayName": "Sonnet 4.6",
    },
    {
        "model": "sonnet-4-5",
        "inputPerMTokens": 3,
        "outputPerMTokens": 15,
        "displayName": "Sonnet 4.5",
    },
    {
        "model": "haiku-4-5",
        "inputPerMTokens": 1,
        "outputPerMTokens": 5,
        "displayName": "Haiku 4.5",
    },
)
