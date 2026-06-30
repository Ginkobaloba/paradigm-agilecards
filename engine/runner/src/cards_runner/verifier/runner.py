"""Verifier orchestrator -- `verify_card`.

RUNNER_CONTRACT.md "Cold-read verification" / "Result shape": this
module is the single entry point the daemon calls. It parses the
card's acceptance_criteria, runs every deterministic handler in
phase 1, batches the subjective items through the cascading evaluator
in phase 2, and returns a `VerifierResult`.

What it does NOT do:

- It does not touch the store. The result is data; the daemon writes
  it back. This is the same separation `worker_stub` keeps from the
  store: a pure component that reads a card snapshot in, produces a
  result out, and lets the orchestrator decide what to persist.
- It does not implement verifier-skip eligibility. That logic depends
  on `cascade_history` (executor-side) and project config that live
  above the verifier. The daemon decides whether to call
  `verify_card` at all; once called, this module always runs.
- It does not retry on its own internal errors. The contract assigns
  the "retry up to two times then route to blocked" behavior to the
  orchestrator (the daemon); the verifier just reports `VerifierError`
  on a true internal crash so the daemon can count retries.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.types import now_utc_iso
from .handlers import DETERMINISTIC_HANDLERS, HandlerContext, HandlerResult
from .handlers.subjective import (
    SubjectiveBatchResult,
    SubjectiveItemVerdict,
    evaluate_subjective_batch,
)
from .parse import AcceptanceItem, parse_acceptance_block
from .risk_factor import RiskFactor
from .types import CANONICAL_TYPES, SchemaError


log = logging.getLogger(__name__)


# Verifier verdict strings. Mirrors the contract names exactly.
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_STANDUP = "needs_standup_review"


class VerifierError(Exception):
    """The verifier orchestrator itself crashed (not an item failing).

    Per RUNNER_CONTRACT.md "Result shape" -- the daemon retries up to
    two times on this; after the second retry, the card moves to
    `blocked/`.
    """


@dataclass(frozen=True)
class ItemResult:
    """One AC item's per-phase result. Mirrors the contract shape."""

    item: dict[str, Any]
    handler_result: HandlerResult
    phase: str  # "deterministic" | "subjective" | "schema_error"
    item_idx: int


@dataclass(frozen=True)
class VerifierResult:
    """The verifier's output for one card.

    `overall_status` is one of `pass`, `fail`, `needs_standup_review`.
    `cascade_history_appendix` is the list of new entries to append
    (not replace) to the card's `verifier_cascade_history`.
    `standup_reason_items` is the item indices that drove a
    `needs_standup_review` outcome; empty otherwise.
    """

    overall_status: str
    items: tuple[ItemResult, ...]
    cascade_history_appendix: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    standup_reason_items: tuple[int, ...] = field(default_factory=tuple)
    standup_reasons: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""
    # Gate chunk 1: structured risk factors the subjective evaluator
    # enumerated (spec section 3.6). Default empty for backward compat --
    # a verifier that emits none, or a deterministic-only card, carries
    # an empty tuple. No gate consumes this yet; it rides along until the
    # confidence-gate skeleton lands.
    risk_factors: tuple[RiskFactor, ...] = field(default_factory=tuple)

    @property
    def failed_items(self) -> tuple[ItemResult, ...]:
        return tuple(it for it in self.items if not it.handler_result.passed)


def verify_card(
    *,
    card_id: str,
    card_body: str,
    worktree: Path,
    env: dict[str, str] | None = None,
    subjective_client: Any | None = None,
    subjective_starting_tier: str = "haiku",
    subjective_max_tier: str = "opus",
    subjective_confidence_threshold: float = 0.85,
    subjective_disabled: bool = False,
    verifier_cost_cap_usd: float | None = None,
    shell_timeout_sec: float = 60.0,
    max_output_tokens: int = 1024,
) -> VerifierResult:
    """Run the two-path verifier on one card.

    Parameters that match RUNNER_CONTRACT.md project-config knobs use
    the contract defaults. `subjective_client` is None when the card
    has no subjective items; the daemon may still pass one (it costs
    nothing if unused).
    """
    try:
        items = parse_acceptance_block(card_body)
    except SchemaError as exc:
        log.warning("card %s schema error: %s", card_id, exc)
        synthetic = ItemResult(
            item={"description": "schema validation"},
            handler_result=HandlerResult(False, {"error": str(exc)}),
            phase="schema_error",
            item_idx=-1,
        )
        return VerifierResult(
            overall_status=VERDICT_FAIL,
            items=(synthetic,),
            notes=f"acceptance_criteria schema error: {exc}",
        )

    ctx = HandlerContext(
        worktree=worktree,
        env=dict(env or {}),
        shell_timeout_sec=shell_timeout_sec,
    )

    deterministic_items, subjective_items = _split_phases(items)

    deterministic_results = tuple(
        _run_deterministic(it, ctx) for it in deterministic_items
    )

    subjective_results, batch_result = _run_subjective_phase(
        card_id=card_id,
        card_body=card_body,
        items=subjective_items,
        client=subjective_client,
        starting_tier=subjective_starting_tier,
        max_tier=subjective_max_tier,
        confidence_threshold=subjective_confidence_threshold,
        disabled=subjective_disabled,
        cost_cap_usd=verifier_cost_cap_usd,
        max_output_tokens=max_output_tokens,
    )

    all_items = _merge_in_declaration_order(
        items, deterministic_results, subjective_results
    )

    overall = _overall_status(all_items, batch_result)
    standup_reasons = _build_standup_reasons(batch_result, items)
    return VerifierResult(
        overall_status=overall,
        items=all_items,
        cascade_history_appendix=tuple(batch_result.cascade_appendix),
        standup_reason_items=batch_result.standup_items,
        standup_reasons=standup_reasons,
        notes=_summary_note(all_items, batch_result),
        risk_factors=batch_result.risk_factors,
    )


# ---- phase split + dispatch ---------------------------------------


def _split_phases(
    items: list[AcceptanceItem],
) -> tuple[list[AcceptanceItem], list[AcceptanceItem]]:
    det: list[AcceptanceItem] = []
    sub: list[AcceptanceItem] = []
    for it in items:
        # Defensive: an unknown canonical type should already have
        # raised at parse time; this assert is a tripwire if a new
        # type gets added to CANONICAL_TYPES without a handler.
        if it.type not in CANONICAL_TYPES:
            raise VerifierError(
                f"type {it.type!r} is canonical but has no handler dispatch"
            )
        (sub if it.subjective else det).append(it)
    return det, sub


def _run_deterministic(
    item: AcceptanceItem, ctx: HandlerContext
) -> ItemResult:
    handler = DETERMINISTIC_HANDLERS.get(item.type)
    if handler is None:
        return ItemResult(
            item=item.raw,
            handler_result=HandlerResult(
                False, {"error": f"no handler for type {item.type!r}"}
            ),
            phase="schema_error",
            item_idx=item.index,
        )
    try:
        result = handler(item.raw, ctx)
    except Exception as exc:  # noqa: BLE001
        log.exception("deterministic handler for %s crashed", item.type)
        result = HandlerResult(False, {"error": f"handler crashed: {exc}"})
    return ItemResult(
        item=item.raw,
        handler_result=result,
        phase="deterministic",
        item_idx=item.index,
    )


def _run_subjective_phase(
    *,
    card_id: str,
    card_body: str,
    items: list[AcceptanceItem],
    client: Any | None,
    starting_tier: str,
    max_tier: str,
    confidence_threshold: float,
    disabled: bool,
    cost_cap_usd: float | None,
    max_output_tokens: int,
) -> tuple[tuple[ItemResult, ...], SubjectiveBatchResult]:
    """Run the subjective phase or short-circuit it."""
    empty = SubjectiveBatchResult(
        final_verdicts=(),
        cascade_appendix=(),
        standup_items=(),
    )
    if not items:
        return (), empty

    if disabled:
        # RUNNER_CONTRACT.md: "With cascade disabled, every subjective
        # item routes straight to `awaiting_standup_review/` without
        # any model call."
        now = now_utc_iso()
        verdicts = tuple(
            SubjectiveItemVerdict(
                item_idx=it.index,
                tier="disabled",
                model="(none)",
                result="fail",
                confidence=0.0,
                reasoning="subjective_cascade_disabled: true",
                at=now,
            )
            for it in items
        )
        appendix = tuple(
            {
                "tier_attempted": "disabled",
                "model": "(none)",
                "confidence": 0.0,
                "result": "fail",
                "reasoning": "subjective_cascade_disabled: true",
                "at": now,
                "item_idx": v.item_idx,
            }
            for v in verdicts
        )
        batch = SubjectiveBatchResult(
            final_verdicts=verdicts,
            cascade_appendix=appendix,
            standup_items=tuple(it.index for it in items),
        )
        return _verdicts_to_item_results(items, batch), batch

    if client is None:
        # The daemon should always provide a client when the card has
        # subjective items. Treat a missing client as cascade-disabled
        # so we route to standup review rather than auto-passing on
        # subjective claims.
        log.warning(
            "card %s has subjective items but no subjective_client; "
            "routing to standup review", card_id,
        )
        return _run_subjective_phase(
            card_id=card_id,
            card_body=card_body,
            items=items,
            client=client,
            starting_tier=starting_tier,
            max_tier=max_tier,
            confidence_threshold=confidence_threshold,
            disabled=True,
            cost_cap_usd=cost_cap_usd,
            max_output_tokens=max_output_tokens,
        )

    batch = evaluate_subjective_batch(
        card_id=card_id,
        card_body=card_body,
        items=items,
        client=client,
        starting_tier=starting_tier,
        max_tier=max_tier,
        confidence_threshold=confidence_threshold,
        cost_cap_usd=cost_cap_usd,
        max_output_tokens=max_output_tokens,
    )
    return _verdicts_to_item_results(items, batch), batch


def _verdicts_to_item_results(
    items: list[AcceptanceItem], batch: SubjectiveBatchResult
) -> tuple[ItemResult, ...]:
    by_idx = {v.item_idx: v for v in batch.final_verdicts}
    standup_set = set(batch.standup_items)
    out: list[ItemResult] = []
    for it in items:
        v = by_idx.get(it.index)
        if v is None:
            handler_result = HandlerResult(
                False,
                {
                    "error": "subjective evaluator returned no verdict "
                             "for this item",
                },
            )
        else:
            passed = v.result == "pass" and it.index not in standup_set
            handler_result = HandlerResult(
                passed=passed,
                evidence={
                    "result": v.result,
                    "confidence": round(v.confidence, 4),
                    "model": v.model,
                    "tier_settled": v.tier,
                    "reasoning": v.reasoning,
                    "needs_standup_review": it.index in standup_set,
                },
            )
        out.append(
            ItemResult(
                item=it.raw,
                handler_result=handler_result,
                phase="subjective",
                item_idx=it.index,
            )
        )
    return tuple(out)


def _merge_in_declaration_order(
    items: list[AcceptanceItem],
    det_results: tuple[ItemResult, ...],
    sub_results: tuple[ItemResult, ...],
) -> tuple[ItemResult, ...]:
    by_idx: dict[int, ItemResult] = {}
    for r in det_results:
        by_idx[r.item_idx] = r
    for r in sub_results:
        by_idx[r.item_idx] = r
    ordered: list[ItemResult] = []
    for it in items:
        result = by_idx.get(it.index)
        if result is not None:
            ordered.append(result)
    return tuple(ordered)


def _overall_status(
    items: tuple[ItemResult, ...], batch: SubjectiveBatchResult
) -> str:
    if not items:
        # No AC items -> nothing to verify. Treat as pass (the daemon
        # may still gate on `requires_pre_approval` etc., separately).
        return VERDICT_PASS
    if batch.standup_items:
        return VERDICT_STANDUP
    if any(not it.handler_result.passed for it in items):
        return VERDICT_FAIL
    return VERDICT_PASS


def _build_standup_reasons(
    batch: SubjectiveBatchResult, items: list[AcceptanceItem]
) -> tuple[str, ...]:
    if not batch.standup_items:
        return ()
    by_idx_item = {it.index: it for it in items}
    by_idx_verdict = {v.item_idx: v for v in batch.final_verdicts}
    out: list[str] = []
    for idx in batch.standup_items:
        item = by_idx_item.get(idx)
        verdict = by_idx_verdict.get(idx)
        descr = item.description if item is not None else f"AC#{idx}"
        if verdict is not None:
            out.append(
                f"AC#{idx} ({descr}): cascade exhausted at tier "
                f"{verdict.tier} with confidence "
                f"{verdict.confidence:.2f}"
            )
        else:
            out.append(f"AC#{idx} ({descr}): no verdict from cascade")
    return tuple(out)


def _summary_note(
    items: tuple[ItemResult, ...], batch: SubjectiveBatchResult
) -> str:
    total = len(items)
    failed = sum(1 for it in items if not it.handler_result.passed)
    standup = len(batch.standup_items)
    return (
        f"verifier: {total} item(s) evaluated, {total - failed} passed, "
        f"{failed} failed, {standup} need standup review"
    )
