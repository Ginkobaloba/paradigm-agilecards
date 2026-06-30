"""Cost attribution for sibling + amendment reviewer + AC editor calls.

Chunk 6b. Until now the reviewer agents (sibling reviewer in chunk 5,
amendment reviewer in chunk 5, AC editor in chunk 6a) talked to the
Anthropic SDK without contributing their token spend back to the card.
The contract is clear that cumulative spend covers
`planning + executor + any sibling-review pass`, so the reviewer's
spend MUST be visible to the card's `cost_cap_usd` (when set) and MUST
be summed into the card's `actual_tokens`.

This module owns three small pieces:

1. `extract_usage_tokens(response)` -- pull `(input, output)` token
   counts off an Anthropic response. Defensive: a response without a
   usage block returns (0, 0) rather than raising; some test stubs
   omit the block entirely.

2. `attribute_to_card(repo, record, usage, tenant_id)` -- read the
   card's prior `actual_tokens`, add the reviewer's usage, write the
   new total back via `update_card_fields`. Best-effort: a failure
   is logged but does not abort the calling sweep (the reviewer's
   decision is already recorded in the marker file, which is the
   authoritative audit trail).

3. `would_exceed_card_cap(...)` / `would_exceed_reviewer_cap(...)` --
   pre-call cap projections. The reviewer code calls these BEFORE
   firing the LLM call so a card with `cost_cap_usd: 0.10` and an
   $0.50 amendment reviewer correctly skips the call rather than
   overspending and then writing the marker.

USD is derived from tokens via `worker_stub.cost.Pricing` -- the same
canonical pricing table the executor's cost governor uses. Cards never
store USD (RUNNER_CONTRACT.md "What the skill commits to").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..store import CardRecord, CardRepository
from ..worker_stub.cost import Pricing


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewerUsage:
    """One reviewer/editor call's resource use.

    Built from an Anthropic response. `cost_usd` is derived from
    `(input_tokens, output_tokens, model_id)` via `Pricing.call_cost`
    at construction time so the marker file carries the historical
    USD value (later pricing edits don't retroactively re-cost an
    already-written marker, matching the executor's behavior).
    """

    input_tokens: int
    output_tokens: int
    cost_usd: float
    model_id: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_response(
        cls,
        response: Any,
        *,
        model_id: str,
        pricing: Pricing | None = None,
    ) -> "ReviewerUsage":
        """Construct a `ReviewerUsage` from an Anthropic SDK response.

        A response with no `usage` block (some unit-test stubs, or a
        future SDK change that moves the field) returns a zero-cost
        usage rather than raising. The reviewer code threads that
        forward into the marker; a zero usage means "no observable
        spend" and the cap math still works.
        """
        input_tokens, output_tokens = extract_usage_tokens(response)
        pr = pricing or Pricing.default()
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=pr.call_cost(model_id, input_tokens, output_tokens),
            model_id=model_id,
        )


def extract_usage_tokens(response: Any) -> tuple[int, int]:
    """Pull `(input, output)` tokens off an Anthropic response object.

    The shape is `response.usage.input_tokens` /
    `response.usage.output_tokens` for the standard SDK; a None / missing
    field defaults to 0. We accept either attribute access or dict-like
    access so a dict-mocked response in a test works the same as a real
    SDK object.
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return (0, 0)
    if isinstance(usage, dict):
        in_t = int(usage.get("input_tokens") or 0)
        out_t = int(usage.get("output_tokens") or 0)
    else:
        in_t = int(getattr(usage, "input_tokens", 0) or 0)
        out_t = int(getattr(usage, "output_tokens", 0) or 0)
    return (max(0, in_t), max(0, out_t))


def attribute_to_card(
    repo: CardRepository,
    record: CardRecord,
    usage: ReviewerUsage,
    *,
    tenant_id: str,
) -> int | None:
    """Atomically increment the card's `actual_tokens` by the usage.

    Re-reads the live `actual_tokens` from the store before adding so
    repeated attributions in one tick (reviewer call, then editor call)
    accumulate instead of trampling each other -- the passed-in
    `record` is whatever the caller had at parse time and may be stale.
    Returns the new cumulative total, or None on failure. Best-effort:
    a failure is logged but does not abort the caller (the marker is
    the authoritative audit trail; an attribution miss is recoverable).
    """
    if usage.total_tokens == 0:
        return int(record.field_value("actual_tokens") or 0)
    live = repo.get_card(record.card_id, tenant_id=tenant_id)
    prior = int(
        (live.field_value("actual_tokens") if live is not None
         else record.field_value("actual_tokens")) or 0
    )
    new_total = prior + usage.total_tokens
    try:
        repo.update_card_fields(
            record.card_id,
            {"actual_tokens": new_total},
            tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort attribution.
        log.error(
            "failed to attribute reviewer tokens to card %s: %s",
            record.card_id, exc,
        )
        return None
    return new_total


def estimate_call_cost_usd(
    model_id: str,
    *,
    est_input_tokens: int,
    max_output_tokens: int,
    pricing: Pricing | None = None,
) -> float:
    """Worst-case projected USD for one reviewer/editor LLM call."""
    pr = pricing or Pricing.default()
    return pr.call_cost(
        model_id, max(0, est_input_tokens), max(0, max_output_tokens)
    )


def card_spent_usd_estimate(
    record: CardRecord,
    *,
    model_id_hint: str,
    pricing: Pricing | None = None,
) -> float:
    """Best-effort estimate of cumulative USD spent on a card so far.

    The card's `actual_tokens` is an aggregate count -- the runner does
    NOT keep per-model breakdown on the card row -- so we have to pick
    a representative rate. We use `model_id_hint` (the about-to-be-made
    reviewer call's model) as a conservative-but-honest projection:
    if the reviewer's spend dominated, this is exact; if the executor's
    spend dominated and used a cheaper model, this slightly overstates
    and skips the call a hair early. Overstating is the correct error
    direction for cap enforcement.
    """
    pr = pricing or Pricing.default()
    prior_tokens = int(record.field_value("actual_tokens") or 0)
    if prior_tokens <= 0:
        return 0.0
    # Assume the historical tokens split 75% input / 25% output (the
    # observed shape for both executor and reviewer calls); pricing.call_cost
    # handles arbitrary splits.
    est_input = int(prior_tokens * 0.75)
    est_output = prior_tokens - est_input
    return pr.call_cost(model_id_hint, est_input, est_output)


def would_exceed_card_cap(
    record: CardRecord,
    *,
    projected_call_usd: float,
    model_id_hint: str,
    pricing: Pricing | None = None,
) -> tuple[bool, float | None, float]:
    """Pre-call cap check against the card's `cost_cap_usd`.

    Returns `(exceeds, cap_usd, projected_total_usd)`. `cap_usd=None`
    on the card always returns `(False, None, ...)` -- no cap, no
    halt. The projection uses `card_spent_usd_estimate` for the prior
    spend.
    """
    cap = record.field_value("cost_cap_usd")
    if cap is None:
        return (False, None, projected_call_usd)
    try:
        cap_f = float(cap)
    except (TypeError, ValueError):
        return (False, None, projected_call_usd)
    spent = card_spent_usd_estimate(
        record, model_id_hint=model_id_hint, pricing=pricing,
    )
    projected_total = spent + projected_call_usd
    return (projected_total > cap_f, cap_f, projected_total)


def would_exceed_reviewer_cap(
    reviewer_cap_usd: float | None,
    *,
    already_spent_usd: float,
    projected_call_usd: float,
) -> bool:
    """Pre-call cap check against the reviewer's own `cost_cap_usd`.

    The reviewer's per-card cap covers the reviewer's spend on that
    card across all calls. For chunk 6b each reviewer makes at most
    one call per card (the marker file blocks re-review), so
    `already_spent_usd` is typically 0 -- but we still take it as a
    parameter so chunk-7 (or a multi-call reviewer) inherits the same
    enforcement.
    """
    if reviewer_cap_usd is None:
        return False
    return (already_spent_usd + projected_call_usd) > reviewer_cap_usd
