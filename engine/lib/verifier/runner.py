"""Verifier orchestrator.

The single entry point: `verify_card`. Given the parsed AC items
from a card (plus optional card body and subjective evidence), runs
the deterministic phase, then the subjective batch phase if any
subjective items are present, and returns a `VerifierResult` the
runner consumes.

The runner is responsible for:

- Reading and writing the card file. This module accepts already-
  parsed data so the same code path is testable without any disk.
- Persisting the cascade history to the card after a subjective pass.
- Moving the card to `awaiting_standup_review/` when the verifier
  result includes any `needs_standup_review` outcome.

This module deliberately stays narrow: no card-parsing logic, no
file moves, no logging policy. The runner integrates those concerns
against the structured result this module returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from verifier.handlers import (
    command,
    file_absent,
    file_absent_content,
    file_contains,
    file_exists,
    http_contains,
    http_status,
    python_assert,
    subjective,
)
from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult, ItemResult
from verifier.schema import validate_ac_items
from verifier.types import ACType, SUBJECTIVE_TYPES


# Dispatch table. Maps the canonical type string to the handler's
# `run(item, *, worktree, project_cfg) -> HandlerResult` callable.
# The subjective handler dispatches to the batch path via the
# orchestrator below; it's still listed here so a hand-edited card
# with a single subjective item routes correctly even when called
# through the per-item entry point.
HANDLER_REGISTRY: dict[str, Callable[..., HandlerResult]] = {
    ACType.FILE_EXISTS.value: file_exists.run,
    ACType.FILE_ABSENT.value: file_absent.run,
    ACType.FILE_CONTAINS.value: file_contains.run,
    ACType.FILE_ABSENT_CONTENT.value: file_absent_content.run,
    ACType.COMMAND.value: command.run,
    ACType.PYTHON_ASSERT.value: python_assert.run,
    ACType.HTTP_STATUS.value: http_status.run,
    ACType.HTTP_CONTAINS.value: http_contains.run,
    ACType.SUBJECTIVE.value: subjective.run,
}


@dataclass(frozen=True)
class VerifierResult:
    """Structured result returned to the runner / dashboard.

    `overall_status` is one of:
        - "pass":  every item passed (deterministic and subjective).
        - "fail":  at least one deterministic item failed, or a
                   subjective item received a high-confidence fail.
        - "needs_standup_review": at least one subjective item could
                   not reach confidence threshold even at the cap
                   tier. The card moves to `awaiting_standup_review/`
                   rather than `active/` or `done/`. The runner
                   stamps `standup_reason` from `standup_reason_items`.

    `cascade_history_appendix` is the new entries the runner should
    append (NOT REPLACE) to the card's `verifier_cascade_history`
    field.
    """

    overall_status: str
    items: tuple[ItemResult, ...] = field(default_factory=tuple)
    cascade_history_appendix: tuple[dict[str, Any], ...] = field(
        default_factory=tuple
    )
    standup_reason_items: tuple[int, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.overall_status == "pass"


def verify_card(
    *,
    ac_items: Sequence[Mapping[str, Any]],
    card_body: str = "",
    subjective_evidence: Mapping[str, Any] | None = None,
    worktree: Path,
    project_cfg: ProjectConfig | None = None,
    card_id: str | None = None,
    card_points: int | None = None,
    call_evaluator: Any | None = None,
) -> VerifierResult:
    """Run the full two-path verification against a card.

    Parameters mirror what a runner can extract from a parsed card
    plus the project config. `call_evaluator` is an injection hook
    for tests; production callers leave it None.
    """
    cfg = project_cfg or ProjectConfig()

    # Defense in depth (locked answer 8): validate the AC list at
    # runtime even though the planner is supposed to have done so
    # at write time. A hand-edited card with a malformed item gets
    # surfaced here as a structured failure.
    report = validate_ac_items(
        list(ac_items),
        card_id=card_id,
        card_points=card_points,
        network_checks_allowed=cfg.network_checks_allowed,
    )
    if not report.ok:
        # Build a synthetic failure result so the caller doesn't have
        # to special-case schema errors. The first failing item index
        # (or 0 if the issue is list-level) carries the message.
        synthetic: list[ItemResult] = []
        for issue in report.issues:
            idx = issue.item_idx if issue.item_idx is not None else 0
            synthetic.append(
                ItemResult(
                    idx=idx,
                    item={"schema_error": issue.message},
                    handler_result=HandlerResult(
                        passed=False,
                        evidence={"schema_error": issue.message},
                    ),
                    phase="deterministic",
                )
            )
        return VerifierResult(
            overall_status="fail",
            items=tuple(synthetic),
        )

    results: list[ItemResult] = []
    subjective_pending: list[tuple[int, Mapping[str, Any]]] = []

    # Pass 1: deterministic items.
    for idx, item in enumerate(ac_items):
        t = item["type"]
        if t in SUBJECTIVE_TYPES:
            subjective_pending.append((idx, item))
            continue
        handler = HANDLER_REGISTRY[t]
        try:
            handler_result = handler(
                item, worktree=worktree, project_cfg=cfg
            )
        except Exception as exc:  # noqa: BLE001
            handler_result = HandlerResult(
                passed=False,
                evidence={
                    "type": t,
                    "error": (
                        f"handler raised {type(exc).__name__}: {exc}; this "
                        "is a verifier bug, not a card failure. Surface to "
                        "Drew."
                    ),
                },
            )
        results.append(
            ItemResult(
                idx=idx,
                item=dict(item),
                handler_result=handler_result,
                phase="deterministic",
            )
        )

    # Pass 2: subjective batch.
    cascade_appendix: list[dict[str, Any]] = []
    standup_indices: list[int] = []

    if subjective_pending:
        ev = dict(subjective_evidence or {})
        pending_items = [it for _, it in subjective_pending]
        outcomes = subjective.evaluate_subjective_batch(
            items=pending_items,
            card_body=card_body,
            evidence=ev,
            project_cfg=cfg,
            call_evaluator=call_evaluator,
        )
        for (orig_idx, item), outcome in zip(subjective_pending, outcomes):
            for attempt in outcome.cascade_history:
                entry = attempt.to_dict()
                entry["item_idx"] = orig_idx
                cascade_appendix.append(entry)
            handler_result = subjective._outcome_to_handler_result(
                outcome, item=item
            )
            results.append(
                ItemResult(
                    idx=orig_idx,
                    item=dict(item),
                    handler_result=handler_result,
                    phase="subjective",
                )
            )
            if outcome.verdict == "needs_standup_review":
                standup_indices.append(orig_idx)

    results.sort(key=lambda r: r.idx)

    if standup_indices:
        status = "needs_standup_review"
    elif all(r.passed for r in results):
        status = "pass"
    else:
        status = "fail"

    return VerifierResult(
        overall_status=status,
        items=tuple(results),
        cascade_history_appendix=tuple(cascade_appendix),
        standup_reason_items=tuple(sorted(standup_indices)),
    )
