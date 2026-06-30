"""Handler: subjective.

LLM-mediated evaluation of an AC item that cannot be expressed as a
deterministic check. Per locked answer 6 in the v1.3 design doc, the
evaluator runs as a cascade haiku -> sonnet -> opus, escalating one
tier whenever the model returns confidence below threshold.

If the final tier (opus) still cannot reach the threshold, the
verifier returns a special outcome (`needs_standup_review`) that
the orchestrator translates into a card move to
`awaiting_standup_review/`. The card does NOT auto-pass and does
NOT auto-fail in that case. AC is the last line of defense before
deployment; a verifier that cannot reach a verdict surfaces the
question to a human.

This module is structured around a `_call_evaluator` indirection so
tests can mock the network call without touching the production
codepath that ships with the Anthropic SDK.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult


# Tier name -> exact model string. Centralized so a future model
# pin update only touches this dict. Drew has explicit guidance that
# subjective starts at haiku and escalates up through opus.
_TIER_TO_MODEL: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

_TIER_ORDER: tuple[str, ...] = ("haiku", "sonnet", "opus")


@dataclass(frozen=True)
class CascadeAttempt:
    """One pass through the cascade. Append-only on the card."""

    tier_attempted: str
    model: str
    confidence: float
    result: str  # "pass" | "fail"
    reasoning: str
    at: str  # ISO 8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier_attempted": self.tier_attempted,
            "model": self.model,
            "confidence": self.confidence,
            "result": self.result,
            "reasoning": self.reasoning,
            "at": self.at,
        }


@dataclass(frozen=True)
class SubjectiveOutcome:
    """The verdict on one subjective AC item after the cascade.

    `verdict` is one of:
        - "pass": evaluator returned pass with confidence above threshold.
        - "fail": evaluator returned fail with confidence above threshold.
        - "needs_standup_review": cascade exhausted without reaching
          threshold. Runner translates this into a move to
          `awaiting_standup_review/` and a `standup_reason` field on
          the card.
    """

    verdict: str
    final_confidence: float
    cascade_history: list[CascadeAttempt] = field(default_factory=list)
    final_reasoning: str = ""


# Type alias for the evaluator callable. Tests inject this; production
# uses `_default_call_evaluator` which dispatches to the Anthropic SDK.
EvaluatorCallable = Callable[
    [str, str, str, list[dict[str, Any]], str],
    dict[str, Any],
]


def run(
    item: Mapping[str, Any],
    *,
    worktree: Path,
    project_cfg: ProjectConfig,
) -> HandlerResult:
    """Single-item entry point.

    Most callers should use `evaluate_subjective_batch` to amortize
    the cascade across multiple subjective items on the same card.
    This `run` exists so the runner's dispatch table is uniform; it
    delegates to the batch variant with a single-item list.
    """
    outcome = evaluate_subjective_batch(
        items=[dict(item)],
        card_body="",
        evidence={"index_0": item.get("evidence_required", "")},
        project_cfg=project_cfg,
    )[0]
    return _outcome_to_handler_result(outcome, item=item)


def evaluate_subjective_batch(
    *,
    items: Sequence[Mapping[str, Any]],
    card_body: str,
    evidence: Mapping[str, Any],
    project_cfg: ProjectConfig,
    call_evaluator: EvaluatorCallable | None = None,
) -> list[SubjectiveOutcome]:
    """Run the cascade against a batch of subjective items.

    A single cascade run handles every subjective item on the card,
    so tier-6 cards that pile up multiple subjective items still cost
    at most one call per tier visited.

    `evidence` is a mapping from `index_<N>` (where N is the position
    of the item in `items`) to the executor's evidence string for
    that item. The runner builds this from the card's
    `subjective_evidence:` block (locked answer 7).
    """
    if not items:
        return []

    if project_cfg.subjective_cascade_disabled:
        # The card opted out of cascade. Fail closed: subjective items
        # demand human attention if the cascade is disabled, since
        # no model verdict will be obtained.
        return [
            SubjectiveOutcome(
                verdict="needs_standup_review",
                final_confidence=0.0,
                cascade_history=[],
                final_reasoning=(
                    "subjective_cascade_disabled is true in project "
                    "config; subjective items cannot be auto-evaluated"
                ),
            )
            for _ in items
        ]

    threshold = project_cfg.subjective_confidence_threshold
    start = project_cfg.subjective_starting_tier
    cap = project_cfg.subjective_max_tier
    tiers = _tier_path(start=start, cap=cap)

    evaluator = call_evaluator or _default_call_evaluator

    histories: list[list[CascadeAttempt]] = [[] for _ in items]
    final_per_item: list[SubjectiveOutcome | None] = [None] * len(items)

    # The cascade runs as a single conversation per tier across all
    # items, not per-item. This is the "batched Haiku call" the
    # design doc calls out.
    pending_indices = list(range(len(items)))

    for tier_idx, tier in enumerate(tiers):
        if not pending_indices:
            break
        model = _TIER_TO_MODEL[tier]
        pending_items = [items[i] for i in pending_indices]
        pending_evidence = {
            f"index_{i}": evidence.get(f"index_{i}", "") for i in pending_indices
        }

        response = _safe_call(
            evaluator,
            tier=tier,
            model=model,
            card_body=card_body,
            items=[dict(it) for it in pending_items],
            evidence_json=json.dumps(pending_evidence),
        )

        # The evaluator MUST return one entry per pending item. We
        # validate shape and treat any malformed entry as a
        # low-confidence fail (which gets escalated).
        next_pending: list[int] = []
        per_item = response.get("items", [])
        if len(per_item) != len(pending_items):
            # Treat the whole batch as low-confidence at this tier so
            # the cascade escalates; per-item history records that.
            for offset, orig_idx in enumerate(pending_indices):
                histories[orig_idx].append(
                    CascadeAttempt(
                        tier_attempted=tier,
                        model=model,
                        confidence=0.0,
                        result="fail",
                        reasoning=(
                            "evaluator returned malformed batch "
                            "(item count mismatch); escalating"
                        ),
                        at=_now_iso(),
                    )
                )
            next_pending = list(pending_indices)
        else:
            for offset, orig_idx in enumerate(pending_indices):
                entry = per_item[offset] if offset < len(per_item) else {}
                attempt = _attempt_from_entry(
                    entry=entry, tier=tier, model=model
                )
                histories[orig_idx].append(attempt)
                if attempt.confidence >= threshold:
                    final_per_item[orig_idx] = SubjectiveOutcome(
                        verdict=attempt.result,
                        final_confidence=attempt.confidence,
                        cascade_history=list(histories[orig_idx]),
                        final_reasoning=attempt.reasoning,
                    )
                else:
                    next_pending.append(orig_idx)

        pending_indices = next_pending

    # Anything still pending after the cascade exhausted goes to
    # standup review with the full history attached.
    for orig_idx in pending_indices:
        last = histories[orig_idx][-1] if histories[orig_idx] else None
        final_per_item[orig_idx] = SubjectiveOutcome(
            verdict="needs_standup_review",
            final_confidence=last.confidence if last is not None else 0.0,
            cascade_history=list(histories[orig_idx]),
            final_reasoning=(
                "cascade exhausted without reaching confidence "
                f"threshold {project_cfg.subjective_confidence_threshold}; "
                "human review required"
            ),
        )

    # mypy: every slot is filled at this point.
    return [
        outcome if outcome is not None else SubjectiveOutcome(
            verdict="needs_standup_review",
            final_confidence=0.0,
            cascade_history=[],
            final_reasoning="internal: no outcome recorded for item",
        )
        for outcome in final_per_item
    ]


def _outcome_to_handler_result(
    outcome: SubjectiveOutcome,
    *,
    item: Mapping[str, Any],
) -> HandlerResult:
    """Convert a SubjectiveOutcome to a HandlerResult.

    A `needs_standup_review` verdict is encoded as `passed=False`
    with a sentinel marker in evidence; the runner reads the marker
    and translates the card to `awaiting_standup_review/` rather than
    treating it as a normal failure. This keeps the dispatch
    interface uniform.
    """
    needs_review = outcome.verdict == "needs_standup_review"
    return HandlerResult(
        passed=outcome.verdict == "pass",
        evidence={
            "description": item.get("description", ""),
            "verdict": outcome.verdict,
            "final_confidence": outcome.final_confidence,
            "final_reasoning": outcome.final_reasoning,
            "cascade_history": [a.to_dict() for a in outcome.cascade_history],
            "needs_standup_review": needs_review,
        },
    )


def _tier_path(*, start: str, cap: str) -> list[str]:
    """Return the ordered tier names from `start` through `cap`."""
    try:
        start_idx = _TIER_ORDER.index(start)
        cap_idx = _TIER_ORDER.index(cap)
    except ValueError as exc:
        raise ValueError(
            f"subjective_starting_tier / subjective_max_tier must each be "
            f"one of {list(_TIER_ORDER)}; got {start=}, {cap=}"
        ) from exc
    if cap_idx < start_idx:
        raise ValueError(
            "subjective_max_tier must not be lower than "
            "subjective_starting_tier"
        )
    return list(_TIER_ORDER[start_idx : cap_idx + 1])


def _attempt_from_entry(
    *, entry: Mapping[str, Any], tier: str, model: str
) -> CascadeAttempt:
    result_raw = str(entry.get("result", "fail")).lower()
    if result_raw not in {"pass", "fail"}:
        result_raw = "fail"
    try:
        confidence = float(entry.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return CascadeAttempt(
        tier_attempted=tier,
        model=model,
        confidence=confidence,
        result=result_raw,
        reasoning=str(entry.get("reasoning", ""))[:1000],
        at=_now_iso(),
    )


def _safe_call(
    evaluator: EvaluatorCallable,
    *,
    tier: str,
    model: str,
    card_body: str,
    items: list[dict[str, Any]],
    evidence_json: str,
) -> dict[str, Any]:
    try:
        return evaluator(tier, model, card_body, items, evidence_json)
    except Exception as exc:  # noqa: BLE001
        # Treat any evaluator-side exception as a malformed response
        # at this tier. The cascade will escalate.
        return {
            "items": [
                {
                    "result": "fail",
                    "confidence": 0.0,
                    "reasoning": (
                        f"evaluator exception at tier {tier}: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
                for _ in items
            ]
        }


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Default evaluator (Anthropic SDK) -------------------------------------


_EVALUATOR_PROMPT = """\
You are a subjective acceptance-criteria evaluator. You are given a
card body, a list of subjective AC items, and the executor's
evidence. For EACH item, return strict pass/fail plus a confidence
score (0.0 to 1.0) and a one-paragraph reasoning. Use confidence
< 0.85 ONLY when you genuinely cannot reach a verdict; the orchestrator
escalates to a stronger model in that case.

Return strict JSON in this exact shape:

  {"items": [
    {"result": "pass" | "fail", "confidence": 0.0-1.0, "reasoning": "..."},
    ...
  ]}

Do not include any text outside the JSON. Do not wrap the JSON in
markdown fences. The list MUST have exactly one entry per AC item,
in the order given.
"""


def _default_call_evaluator(
    tier: str,
    model: str,
    card_body: str,
    items: list[dict[str, Any]],
    evidence_json: str,
) -> dict[str, Any]:
    """Production evaluator. Uses the Anthropic SDK when available.

    Returns the parsed JSON the model produced. Network and SDK
    errors propagate up; `_safe_call` catches them and converts
    into a malformed-batch response so the cascade escalates.
    """
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "anthropic SDK not installed; install `anthropic` or inject "
            "a custom evaluator via call_evaluator="
        ) from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set; subjective evaluation cannot run"
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_msg = (
        "CARD BODY:\n"
        f"{card_body}\n\n"
        "AC ITEMS:\n"
        f"{json.dumps(items, indent=2)}\n\n"
        "EVIDENCE (keyed by item index):\n"
        f"{evidence_json}\n"
    )
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_EVALUATOR_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"evaluator returned non-JSON at tier {tier}: {exc}; "
            f"raw text (truncated): {text[:200]}"
        ) from exc
    if not isinstance(parsed, dict) or "items" not in parsed:
        raise RuntimeError(
            f"evaluator returned malformed JSON at tier {tier}: "
            f"{text[:200]}"
        )
    return parsed
