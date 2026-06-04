"""Subjective acceptance-criterion evaluator -- one batched cascading call.

RUNNER_CONTRACT.md "Cold-read verification" / "Subjective cascade
behavior":

- Subjective items run only when the card has at least one. They are
  batched into a single evaluator call (not one call per item) and
  re-issued at the next tier on confidence below threshold.
- Tier order: haiku -> sonnet -> opus. Cap: `subjective_max_tier`
  (default opus). Threshold: `subjective_confidence_threshold`
  (default 0.85).
- Each attempt at each tier appends one entry to
  `verifier_cascade_history` PER ITEM evaluated this attempt
  (item_idx, tier_attempted, model, confidence, result, reasoning, at).
- An item that escapes the cap below threshold contributes its index
  to `standup_reason_items`; the card routes to
  `awaiting_standup_review/` rather than auto-pass or auto-fail.

The evaluator client is the same Anthropic SDK shape the executor
uses (`messages.create`), so tests inject the same kind of fake
client. The evaluator does NOT share state with the executor's cost
governor -- the executor's `cost_cap_usd` covers EXECUTION spend; the
verifier carries its own budget knob (`verifier_cost_cap_usd`, when
the card sets it; otherwise the verifier runs without a cap, by
contract).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

from ...common.types import now_utc_iso
from ...worker_stub.cost import CostGovernor, Pricing
from ..risk_factor import RiskFactor, parse_risk_factors


log = logging.getLogger(__name__)


# Tier ladder for the cascade. Maps a coarse tier name to the concrete
# model id and the next tier above it. Mirrors the executor's
# `_TIER_MODEL` in shape but lives here so the verifier can evolve
# (e.g., pick different model versions) without touching the executor.
TIER_MODELS: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

TIER_ORDER: tuple[str, ...] = ("haiku", "sonnet", "opus")


_SUBJECTIVE_SYSTEM_PROMPT = (
    "You are the cold-read verifier in the agile-cards runner. You "
    "receive one work card and the subset of its acceptance-criteria "
    "items marked `type: subjective`. For each item, judge -- from the "
    "card body alone, treating the executor's work as untrusted "
    "evidence -- whether the criterion is satisfied. Be strict and "
    "specific.\n\n"
    "Output a SINGLE JSON object, nothing else, with this exact shape:\n"
    "{\n"
    '  "items": [\n'
    '    {"index": <int>, "result": "pass" | "fail", '
    '"confidence": <float 0.0..1.0>, "reasoning": <string>}\n'
    "  ],\n"
    '  "risk_factors": [\n'
    '    {"kind": <string>, "severity": "low" | "medium" | "high", '
    '"description": <string>, "location": <string or null>, '
    '"source_item_idx": <int or null>}\n'
    "  ]\n"
    "}\n"
    "One `items` entry per subjective item, in the order they were given. "
    "`confidence` is YOUR confidence in YOUR verdict, NOT the executor's "
    "confidence. A low confidence value (below 0.85) signals you want a "
    "stronger model to take a second look at this item.\n\n"
    "`risk_factors` enumerates any code-level risks you noticed in the "
    "card body or evidence, EVEN IF you marked every item as pass. We "
    "will NOT use this list to flip your pass/fail call -- it only "
    "decides who reviews the merge, so report honestly. Mark each "
    "low / medium / high. Use an empty list if you saw none. Useful "
    "kinds include external_call_added, guard_removed, raw_sql, "
    "string_eval, crypto_change, error_swallowed, concurrency_change, "
    "permission_change, unverified_assumption, incomplete_test_coverage, "
    "dep_pin_loosened."
)


@dataclass(frozen=True)
class SubjectiveItemVerdict:
    """One item's evaluator verdict at one cascade tier."""

    item_idx: int
    tier: str
    model: str
    result: str  # "pass" | "fail"
    confidence: float
    reasoning: str
    at: str


@dataclass(frozen=True)
class SubjectiveBatchResult:
    """The whole subjective batch's outcome.

    `final_verdicts` is one verdict per item -- the LAST verdict the
    cascade produced for that item (the one that either settled at or
    above threshold, or hit the cap below threshold).
    `cascade_appendix` is every verdict at every tier, ready to append
    to the card's `verifier_cascade_history`. `standup_items` is the
    set of item indices that exhausted the cap below threshold.
    """

    final_verdicts: tuple[SubjectiveItemVerdict, ...]
    cascade_appendix: tuple[dict[str, Any], ...]
    standup_items: tuple[int, ...]
    # Gate chunk 1: risk factors the evaluator enumerated. Card-level, not
    # per-item. Carries the last (strongest-tier) call's list, since that
    # is the most authoritative read. Empty when no subjective call ran or
    # the model emitted none. No gate consumes this yet.
    risk_factors: tuple[RiskFactor, ...] = ()


def evaluate_subjective_batch(
    *,
    card_id: str,
    card_body: str,
    items: Sequence[Any],  # AcceptanceItem; loose typed to avoid circular import.
    client: Any,
    starting_tier: str = "haiku",
    max_tier: str = "opus",
    confidence_threshold: float = 0.85,
    pricing: Pricing | None = None,
    cost_cap_usd: float | None = None,
    max_output_tokens: int = 1024,
) -> SubjectiveBatchResult:
    """Run the subjective phase of cold-read verification.

    Returns even when the cap exhausts: items that did not reach
    threshold carry their last-tier verdict in `final_verdicts` and
    appear in `standup_items`. The caller (`verifier.runner`) decides
    the card's `overall_status` from that signal.

    On an LLM error or a malformed evaluator response, the affected
    items contribute a `result="fail"` verdict with low confidence and
    the error in `reasoning`; the cascade still escalates so a stronger
    model gets a chance. After the cap, those items end up in
    `standup_items` per the contract.
    """
    if not items:
        return SubjectiveBatchResult(final_verdicts=(), cascade_appendix=(), standup_items=())

    if starting_tier not in TIER_ORDER:
        raise ValueError(f"unknown starting_tier {starting_tier!r}")
    if max_tier not in TIER_ORDER:
        raise ValueError(f"unknown max_tier {max_tier!r}")
    if TIER_ORDER.index(max_tier) < TIER_ORDER.index(starting_tier):
        raise ValueError(
            f"max_tier {max_tier!r} is below starting_tier {starting_tier!r}"
        )

    governor = CostGovernor.create(cost_cap_usd, pricing=pricing)
    tiers_to_try = _tier_window(starting_tier, max_tier)

    # Map of item.index -> "pending" set; we re-prompt with only items
    # that did not settle at this tier or above.
    pending: dict[int, Any] = {item.index: item for item in items}
    final_by_idx: dict[int, SubjectiveItemVerdict] = {}
    appendix: list[dict[str, Any]] = []
    # Keep the risk factors from the latest tier that actually returned
    # them; a stronger model's read supersedes a weaker one's.
    last_risk_factors: tuple[RiskFactor, ...] = ()

    for tier in tiers_to_try:
        if not pending:
            break
        model = TIER_MODELS[tier]
        try:
            governor.before_call(
                model,
                est_input_tokens=_estimate_tokens(card_body, pending.values()),
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - CostCapExceeded mostly.
            log.warning("subjective cascade halting at %s: %s", tier, exc)
            break

        try:
            verdicts, usage_in, usage_out, tier_risks = _one_tier_call(
                client=client,
                model=model,
                tier=tier,
                card_id=card_id,
                card_body=card_body,
                items=list(pending.values()),
                max_output_tokens=max_output_tokens,
            )
            governor.record_call(model, usage_in, usage_out)
            if tier_risks:
                last_risk_factors = tier_risks
        except Exception as exc:  # noqa: BLE001 - SDK / network.
            log.exception("subjective verifier call at %s failed", tier)
            now = now_utc_iso()
            verdicts = tuple(
                SubjectiveItemVerdict(
                    item_idx=int(item.index),
                    tier=tier,
                    model=model,
                    result="fail",
                    confidence=0.0,
                    reasoning=f"verifier call at {tier} raised: {exc}",
                    at=now,
                )
                for item in pending.values()
            )

        # Bookkeep every verdict (settled or not) into the appendix.
        for v in verdicts:
            appendix.append(_verdict_to_appendix_entry(v))

        new_pending: dict[int, Any] = {}
        for v in verdicts:
            final_by_idx[v.item_idx] = v
            if v.confidence < confidence_threshold and tier != tiers_to_try[-1]:
                new_pending[v.item_idx] = pending[v.item_idx]
        pending = new_pending

    final_verdicts = tuple(
        final_by_idx[i] for i in sorted(final_by_idx.keys())
    )
    standup_items = tuple(
        sorted(
            v.item_idx
            for v in final_verdicts
            if v.confidence < confidence_threshold
        )
    )
    return SubjectiveBatchResult(
        final_verdicts=final_verdicts,
        cascade_appendix=tuple(appendix),
        standup_items=standup_items,
        risk_factors=last_risk_factors,
    )


# ---- one call ------------------------------------------------------


def _one_tier_call(
    *,
    client: Any,
    model: str,
    tier: str,
    card_id: str,
    card_body: str,
    items: list[Any],
    max_output_tokens: int,
) -> tuple[tuple[SubjectiveItemVerdict, ...], int, int, tuple[RiskFactor, ...]]:
    """Issue one batched evaluator call and parse the result.

    Returns (verdicts, input_tokens, output_tokens, risk_factors). Token
    counts come
    off the message's `usage` attribute when present (the real SDK
    populates it); fakes can omit it and the cascade still works -- the
    governor just sees zero tokens for that call, which is the right
    default for a token-free unit test.
    """
    user = _build_user_prompt(card_id, card_body, items)
    message = client.messages.create(
        model=model,
        max_tokens=max_output_tokens,
        system=_SUBJECTIVE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = _message_text(message)
    parsed = _parse_evaluator_json(text)
    risk_factors = parse_risk_factors(parsed.get("risk_factors"))
    usage = getattr(message, "usage", None)
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
    now = now_utc_iso()
    by_index = {int(it.index): it for it in items}
    out: list[SubjectiveItemVerdict] = []
    seen_indices: set[int] = set()
    for entry in parsed.get("items", []):
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("index", -1))
        except (TypeError, ValueError):
            continue
        if idx not in by_index or idx in seen_indices:
            continue
        seen_indices.add(idx)
        result = str(entry.get("result", "fail")).lower().strip()
        if result not in ("pass", "fail"):
            result = "fail"
        try:
            conf = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        reasoning = str(entry.get("reasoning") or "")
        out.append(
            SubjectiveItemVerdict(
                item_idx=idx,
                tier=tier,
                model=model,
                result=result,
                confidence=conf,
                reasoning=reasoning,
                at=now,
            )
        )
    # Any item the evaluator dropped is treated as low-confidence fail so
    # the cascade has something to escalate. Without this an evaluator
    # that returns only some items would silently settle the rest.
    for idx, item in by_index.items():
        if idx in seen_indices:
            continue
        out.append(
            SubjectiveItemVerdict(
                item_idx=idx,
                tier=tier,
                model=model,
                result="fail",
                confidence=0.0,
                reasoning="evaluator did not return a verdict for this item",
                at=now,
            )
        )
    out.sort(key=lambda v: v.item_idx)
    return tuple(out), in_tok, out_tok, risk_factors


def _build_user_prompt(card_id: str, card_body: str, items: list[Any]) -> str:
    blocks = []
    for it in items:
        raw = it.raw if hasattr(it, "raw") else {}
        descr = raw.get("description") or f"AC#{it.index}"
        evidence = raw.get("subjective_evidence")
        block = [
            f"- index: {it.index}",
            f"  description: {descr}",
        ]
        if evidence is not None:
            block.append(f"  subjective_evidence: {json.dumps(evidence)}")
        blocks.append("\n".join(block))
    items_yaml = "\n".join(blocks)
    return (
        f"# Card: {card_id}\n\n"
        "## Card body\n\n"
        f"{card_body.strip()}\n\n"
        "## Subjective acceptance items to evaluate\n\n"
        f"{items_yaml}\n\n"
        "Return your verdicts as JSON, exactly as the system prompt "
        "specifies."
    )


def _message_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts).strip()


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _parse_evaluator_json(text: str) -> dict[str, Any]:
    if not text:
        return {"items": []}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        log.warning("subjective evaluator returned non-JSON text")
        return {"items": []}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        log.warning("could not parse evaluator JSON: %s", exc)
        return {"items": []}


def _verdict_to_appendix_entry(v: SubjectiveItemVerdict) -> dict[str, Any]:
    """Build one `verifier_cascade_history` entry from a verdict.

    Shape per RUNNER_CONTRACT.md "Provenance field schema": each entry
    carries `tier_attempted`, `model`, `confidence`, `result`,
    `reasoning`, `at`, `item_idx`.
    """
    return {
        "tier_attempted": v.tier,
        "model": v.model,
        "confidence": round(v.confidence, 4),
        "result": v.result,
        "reasoning": v.reasoning,
        "at": v.at,
        "item_idx": v.item_idx,
    }


def _tier_window(starting_tier: str, max_tier: str) -> list[str]:
    """Return the slice of `TIER_ORDER` from start through max."""
    start = TIER_ORDER.index(starting_tier)
    stop = TIER_ORDER.index(max_tier)
    return list(TIER_ORDER[start: stop + 1])


def _estimate_tokens(card_body: str, items: Any) -> int:
    """Quick char-based projection for the pre-call cost guard."""
    items = list(items) if items is not None else []
    body = card_body or ""
    item_chars = sum(len(json.dumps(getattr(i, "raw", {}))) for i in items)
    return (len(body) + item_chars) // 4 + 32


