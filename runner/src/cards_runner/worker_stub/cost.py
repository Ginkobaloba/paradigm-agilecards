"""Cost metering and cost-cap enforcement for the SDK executor.

RUNNER_CONTRACT.md "Cost cap enforcement": when a card carries
`cost_cap_usd`, the runner tracks cumulative tokens, converts to USD
on each model-call boundary, and halts before it overspends. Chunk
2b-ii makes that real.

The base `anthropic` Messages SDK has no native hook registry (the
Agent SDK does; the runner intentionally depends on the smaller
package). So the "SDK hooks" the runner design calls for are
implemented here as explicit pre-call / pre-tool / post-call
callbacks the `SdkInvoker` fires around every model call. The
enforcement point is exactly the one the contract names -- each
model-call boundary -- and the check itself is sub-millisecond, so a
runaway agent loop is stopped within one turn of breaching. A single
pathologically long call is the Job Object / wall-clock backstop's
job, not this layer's.

USD is always derived from tokens, never stored on a card
(RUNNER_CONTRACT.md "What the skill commits to": cards do NOT store
USD).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any


log = logging.getLogger(__name__)


# Coarse model tiers, cheapest first. Pricing and the executor
# cascade both walk this ladder.
TIER_ORDER: tuple[str, ...] = ("haiku", "sonnet", "opus")

# Per-million-token USD rates keyed by tier: (input $/Mtok, output
# $/Mtok). These are ESTIMATES. The canonical source is the /cards
# skill's `tier_pricing.yaml`, which is not vendored into the runner
# repo; until it is wired in (chunk 3+) the runner uses this table
# plus any override from the CARDS_RUNNER_PRICING_JSON env var. The
# cap math is correct regardless of the absolute figures -- a wrong
# rate only shifts where the cap trips, it does not break the
# enforcement mechanism.
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "haiku": (1.00, 5.00),
    "sonnet": (3.00, 15.00),
    "opus": (15.00, 75.00),
}


def model_tier(model_id: str) -> str:
    """Map a model id to a coarse pricing / cascade tier.

    Substring match keeps it robust against version-dated suffixes:
    `claude-haiku-4-5-20251001` -> `haiku`. An unknown model is priced
    as `opus` so the cap errs toward halting early rather than
    silently overspending.
    """
    low = model_id.lower()
    for tier in TIER_ORDER:
        if tier in low:
            return tier
    log.warning("unknown model %r; pricing it as the opus tier", model_id)
    return "opus"


@dataclass(frozen=True)
class Pricing:
    """Token -> USD conversion table, keyed by model tier."""

    table: dict[str, tuple[float, float]]

    @classmethod
    def default(cls) -> "Pricing":
        """The embedded estimate table, with optional env override.

        `CARDS_RUNNER_PRICING_JSON` is a JSON object mapping a tier
        name to a `[input_rate, output_rate]` pair. A malformed value
        is ignored with a warning rather than crashing the worker.
        """
        merged = dict(_DEFAULT_PRICING)
        raw = os.environ.get("CARDS_RUNNER_PRICING_JSON")
        if raw:
            try:
                override = json.loads(raw)
                for tier, pair in override.items():
                    merged[str(tier).lower()] = (float(pair[0]), float(pair[1]))
            except Exception:  # noqa: BLE001
                log.warning("ignoring malformed CARDS_RUNNER_PRICING_JSON")
        return cls(table=merged)

    def rates(self, model_id: str) -> tuple[float, float]:
        return self.table.get(model_tier(model_id), self.table["opus"])

    def call_cost(
        self, model_id: str, input_tokens: int, output_tokens: int
    ) -> float:
        """USD cost of one call's token usage."""
        rate_in, rate_out = self.rates(model_id)
        return (input_tokens / 1e6) * rate_in + (output_tokens / 1e6) * rate_out


@dataclass
class CostMeter:
    """Cumulative token tally with a derived USD figure."""

    pricing: Pricing
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    _usd: float = 0.0

    def record(
        self, model_id: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Add one call's usage; return the new cumulative USD."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1
        per = self.by_model.setdefault(
            model_id, {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        )
        per["input_tokens"] += input_tokens
        per["output_tokens"] += output_tokens
        per["calls"] += 1
        self._usd += self.pricing.call_cost(model_id, input_tokens, output_tokens)
        return self._usd

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def usd(self) -> float:
        return self._usd

    def snapshot(self) -> dict[str, Any]:
        """A JSON-serializable record of the tally, for event payloads."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "usd": round(self._usd, 6),
            "by_model": {k: dict(v) for k, v in self.by_model.items()},
        }


class CostCapExceeded(Exception):
    """Raised when a card's `cost_cap_usd` is or would be exceeded.

    `stage` is where enforcement fired: `pre_call` (a projected call
    would not fit), `pre_tool` (already over budget before a tool
    dispatch), or `post_call` (the call that just completed pushed
    cumulative spend past the cap).
    """

    def __init__(
        self,
        *,
        cap_usd: float,
        spent_usd: float,
        stage: str,
        projected_usd: float | None = None,
    ) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd
        self.stage = stage
        self.projected_usd = projected_usd
        detail = f"spent ${spent_usd:.4f}"
        if projected_usd is not None:
            detail += f", projected ${projected_usd:.4f}"
        super().__init__(
            f"cost cap ${cap_usd:.4f} exceeded at {stage}: {detail}"
        )


@dataclass
class CostGovernor:
    """Fires the cost-cap hooks around every model call.

    `cap_usd` of `None` means the card set no `cost_cap_usd`; the
    governor then degrades to a pure meter and never raises
    (RUNNER_CONTRACT.md: "When `cost_cap_usd` is null, no cap is
    enforced").
    """

    cap_usd: float | None
    meter: CostMeter

    @classmethod
    def create(
        cls, cap_usd: float | None, *, pricing: Pricing | None = None
    ) -> "CostGovernor":
        return cls(
            cap_usd=cap_usd,
            meter=CostMeter(pricing=pricing or Pricing.default()),
        )

    def before_call(
        self, model_id: str, *, est_input_tokens: int, max_output_tokens: int
    ) -> None:
        """Pre-message hook. Raise if this call's worst case will not fit.

        The projection uses `max_output_tokens` -- the worst the call
        can cost -- so the cap errs toward halting before the spend
        rather than after it. This is the check RUNNER_CONTRACT.md's
        cascade section requires "immediately after each escalation
        against the projected remaining spend at the new tier".
        """
        if self.cap_usd is None:
            return
        if self.meter.usd >= self.cap_usd:
            raise CostCapExceeded(
                cap_usd=self.cap_usd, spent_usd=self.meter.usd, stage="pre_call"
            )
        worst = self.meter.pricing.call_cost(
            model_id, est_input_tokens, max_output_tokens
        )
        projected = self.meter.usd + worst
        if projected > self.cap_usd:
            raise CostCapExceeded(
                cap_usd=self.cap_usd,
                spent_usd=self.meter.usd,
                projected_usd=projected,
                stage="pre_call",
            )

    def before_tool(self, tool_name: str) -> None:
        """Pre-tool-use hook. Cheap budget checkpoint before a tool runs.

        2b-ii's executor is reasoning-only and dispatches no tools, but
        the hook is wired and tested so a tool-equipped executor
        (chunk 3+) inherits enforcement for free.
        """
        del tool_name
        if self.cap_usd is None:
            return
        if self.meter.usd >= self.cap_usd:
            raise CostCapExceeded(
                cap_usd=self.cap_usd, spent_usd=self.meter.usd, stage="pre_tool"
            )

    def record_call(
        self, model_id: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Post-call hook. Record usage; raise if the call breached the cap."""
        spent = self.meter.record(model_id, input_tokens, output_tokens)
        if self.cap_usd is not None and spent > self.cap_usd:
            raise CostCapExceeded(
                cap_usd=self.cap_usd, spent_usd=spent, stage="post_call"
            )
        return spent
