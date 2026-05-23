"""AC-amendment reviewer automation (chunk 5).

Chunk 4 wired the executor->runner side of the amendment protocol: an
executor that detects a broken AC item writes a `change_request:` block,
sets `status: awaiting_amendment_review`, and the runner routes the
card to `amendments/`. The reviewer side -- approve / deny the change
request -- was left to a human.

This module is the chunk 5 reviewer-agent implementation. Each tick it:

1. Lists `amendments` cards.
2. Skips any card that already has a fresh marker at
   `signals/amendment_reviews/<card_id>.json`.
3. For the rest: parses the `change_request:` block from the body,
   calls the configured reviewer client, gets `approve|deny|comment` +
   reasoning, and routes accordingly:

   - **approve.** RUNNER_CONTRACT.md says the reviewer "edits the
     relevant item inside the card's `acceptance_checks:` block". The
     chunk 5 implementation does NOT auto-edit AC; that path is too
     fragile to risk on a first pass. Instead approve routes the card
     to `blocked` with `merge_status=amendment_approved` and a marker
     that a human (or a follow-on `auto_edit_ac: true` mode) uses to
     finalize the AC edit and put the card back in backlog.
   - **deny.** Appends a `change_request_decision:` block to the body
     (per contract: "Do not delete the `change_request:` block; it
     stays as audit trail") and transitions the card to `active` so
     the executor resumes against the original AC.
   - **comment.** No card transition; the marker is written so the
     reviewer doesn't re-spend tokens next tick. The card stays in
     `amendments` for human follow-up.

Per RUNNER_CONTRACT.md the runner "MUST never amend AC on its own
initiative". Delegation is explicit: `amendment_reviewer.enabled: true`
in `project.yaml` is the operator's "yes, this reviewer agent speaks
for me on amendments".
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..common.project_config import ReviewerConfig
from ..common.types import DaemonConfig, RuntimePaths, now_utc_iso
from ..store import (
    DEFAULT_TENANT,
    ActorType,
    CardEvent,
    CardRecord,
    CardRepository,
    CardStatus,
    EventType,
)
from ..verifier.parse import parse_acceptance_block
from .ac_editor import AcEditError, AmendmentEdit, splice_amendment
from .amendment_editor_client import AmendmentEditorClient
from .reviewer_cost import (
    ReviewerUsage,
    attribute_to_card,
    estimate_call_cost_usd,
    would_exceed_card_cap,
    would_exceed_reviewer_cap,
)
from .sibling_reviewer import ReviewerDecision, SiblingReviewerClient


log = logging.getLogger(__name__)


AmendmentAction = Literal[
    "reviewed_approve",
    "reviewed_approve_edited",
    "reviewed_deny",
    "reviewed_comment",
    "skipped_existing",
    "skipped_no_change_request",
    "skipped_cost_cap",
]


@dataclass(frozen=True)
class AmendmentOutcome:
    """One amendment-card outcome from this sweep. For tests + summaries."""

    card_id: str
    action: AmendmentAction
    decision: str | None = None
    reason: str = ""


def run_amendment_reviews(
    *,
    repo: CardRepository,
    cfg: DaemonConfig,
    paths: RuntimePaths,
    reviewer_client: SiblingReviewerClient,
    reviewer_config: ReviewerConfig,
    editor_client: AmendmentEditorClient | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> list[AmendmentOutcome]:
    """Process `amendments` cards once per tick.

    `editor_client` is consulted only when `reviewer_config.auto_edit_ac`
    is True AND the reviewer decision is `approve`. When None, the
    runner behaves as in chunk 5 (approve parks the card in
    `blocked/amendment_approved` for a human to finalize).
    """
    if not cfg.amendment_reviewer_enabled or not reviewer_config.enabled:
        return []
    outcomes: list[AmendmentOutcome] = []
    candidates = repo.query_cards(
        tenant_id=tenant_id, status=CardStatus.AMENDMENTS.value
    )
    for record in candidates:
        outcomes.append(
            _process_card(
                record,
                repo=repo,
                paths=paths,
                reviewer_client=reviewer_client,
                reviewer_config=reviewer_config,
                editor_client=editor_client,
                tenant_id=tenant_id,
            )
        )
    return outcomes


def _process_card(
    record: CardRecord,
    *,
    repo: CardRepository,
    paths: RuntimePaths,
    reviewer_client: SiblingReviewerClient,
    reviewer_config: ReviewerConfig,
    editor_client: AmendmentEditorClient | None,
    tenant_id: str,
) -> AmendmentOutcome:
    marker_path = amendment_review_marker_path(paths, record.card_id)
    if marker_path.is_file():
        return AmendmentOutcome(
            card_id=record.card_id,
            action="skipped_existing",
            reason="marker already present for this card",
        )
    change_request = extract_change_request_block(record.body_md)
    if not change_request:
        log.warning(
            "amendments card %s has no change_request: block; cannot review",
            record.card_id,
        )
        return AmendmentOutcome(
            card_id=record.card_id,
            action="skipped_no_change_request",
            reason="body has no change_request: yaml block",
        )

    # Chunk 6b: pre-call cap projection. Estimate the upcoming
    # reviewer call cost (and, if auto-edit will fire, the editor
    # call cost too) and skip when either the reviewer's own cap or
    # the card's cap would be breached.
    projected_call_usd = _project_reviewer_cost(
        reviewer_client, change_request, record.body_md, reviewer_config,
    )
    if reviewer_config.auto_edit_ac and editor_client is not None:
        projected_call_usd += _project_editor_cost(
            editor_client, change_request, record.body_md, reviewer_config,
        )
    cap_skip = _check_amendment_cost_caps(
        record=record,
        reviewer_config=reviewer_config,
        projected_call_usd=projected_call_usd,
    )
    if cap_skip is not None:
        return cap_skip

    decision = reviewer_client.review(
        card_id=record.card_id,
        card_body=record.body_md,
        pr_diff=change_request,  # the reviewer reads the change request as 'diff'.
        reviewer=reviewer_config,
    )

    # Chunk 6b: attribute the reviewer's tokens to the card before any
    # downstream decision. The editor (if it fires) will attribute its
    # own tokens separately so the breakdown is preserved in the marker.
    card_total_after_review: int | None = None
    if decision.usage is not None:
        card_total_after_review = attribute_to_card(
            repo, record, decision.usage, tenant_id=tenant_id,
        )

    marker: dict[str, Any] = {
        "card_id": record.card_id,
        "decision": decision.decision,
        "reasoning": decision.reasoning,
        "confidence": decision.confidence,
        "model_used": decision.model_used or reviewer_config.model_id,
        "reviewer_label": reviewer_config.label,
        "at": now_utc_iso(),
        "change_request_present": True,
        "cost": _usage_marker_payload(decision, card_total_after_review),
    }

    if decision.decision == "approve":
        # Auto-edit path (chunk 6a). Only fires when the project has
        # opted in AND an editor client is wired. The editor's result
        # (or the reason it was skipped) is recorded in the marker so
        # the audit trail explains which branch ran.
        if reviewer_config.auto_edit_ac and editor_client is not None:
            edit_outcome = _try_auto_edit(
                record,
                repo=repo,
                tenant_id=tenant_id,
                editor_client=editor_client,
                reviewer_config=reviewer_config,
                decision=decision,
                change_request=change_request,
            )
            marker["auto_edit"] = edit_outcome.marker_payload
            _write_marker(marker_path, marker)
            _emit_event(repo, record, decision, marker, tenant_id=tenant_id)
            if edit_outcome.applied:
                return AmendmentOutcome(
                    card_id=record.card_id,
                    action="reviewed_approve_edited",
                    decision="approve",
                    reason=(
                        "amendment approved + auto-edited; "
                        "routed back to backlog"
                    ),
                )
            # Fell through to the human-finalize path; route as the
            # chunk 5 approve-without-edit case.
            _route_approve(
                record,
                repo=repo,
                tenant_id=tenant_id,
                reviewer_config=reviewer_config,
                decision=decision,
            )
            return AmendmentOutcome(
                card_id=record.card_id,
                action="reviewed_approve",
                decision="approve",
                reason=(
                    "amendment approved; auto-edit declined "
                    f"({edit_outcome.fallback_reason}); routed to blocked"
                ),
            )
        _write_marker(marker_path, marker)
        _emit_event(repo, record, decision, marker, tenant_id=tenant_id)
        _route_approve(
            record,
            repo=repo,
            tenant_id=tenant_id,
            reviewer_config=reviewer_config,
            decision=decision,
        )
        return AmendmentOutcome(
            card_id=record.card_id,
            action="reviewed_approve",
            decision="approve",
            reason="amendment approved; routed to blocked for AC edit",
        )

    _write_marker(marker_path, marker)
    _emit_event(repo, record, decision, marker, tenant_id=tenant_id)
    if decision.decision == "request_changes":
        # The reviewer is denying the change request: the existing AC
        # stands and the executor resumes.
        new_body = _append_change_request_decision(
            record.body_md,
            decision=decision,
            reviewer_config=reviewer_config,
            outcome="denied",
        )
        _route_deny(
            record,
            repo=repo,
            tenant_id=tenant_id,
            new_body=new_body,
            reviewer_config=reviewer_config,
            decision=decision,
        )
        return AmendmentOutcome(
            card_id=record.card_id,
            action="reviewed_deny",
            decision="request_changes",
            reason="amendment denied; routed back to active",
        )
    return AmendmentOutcome(
        card_id=record.card_id,
        action="reviewed_comment",
        decision="comment",
        reason="reviewer offered comment only; card stays in amendments",
    )


# ---- routing -------------------------------------------------------


def _route_approve(
    record: CardRecord,
    *,
    repo: CardRepository,
    tenant_id: str,
    reviewer_config: ReviewerConfig,
    decision: ReviewerDecision,
) -> None:
    """Approve path: route to `blocked` with the reviewer's marker on top.

    The contract authorizes a delegated reviewer to edit AC, but
    automated AC editing is brittle enough that chunk 5 stops one step
    short: the runner records the approval and parks the card in
    `blocked` (`merge_status=amendment_approved`). A human (or a
    follow-on auto-edit mode) takes the edit from there and moves the
    card back to `backlog`.
    """
    try:
        repo.transition(
            record.card_id,
            to_status=CardStatus.BLOCKED.value,
            tenant_id=tenant_id,
            fields={
                "merge_status": "amendment_approved",
            },
            actor_id=reviewer_config.label,
            actor_type=ActorType.RUNNER.value,
            event_type=EventType.AMENDED.value,
            payload={
                "source": "amendment_reviewer",
                "outcome": "approved",
                "reason": decision.reasoning,
                "confidence": decision.confidence,
                "model_used": decision.model_used,
                "next_action": (
                    "human or auto-edit mode finalizes AC, then moves "
                    "card back to backlog"
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "amendment_approve transition failed for %s: %s",
            record.card_id, exc,
        )


# ---- auto-edit (chunk 6a) -------------------------------------------


@dataclass(frozen=True)
class _AutoEditOutcome:
    """Internal result of an `auto_edit_ac` attempt."""

    applied: bool
    fallback_reason: str
    marker_payload: dict[str, Any]


def _try_auto_edit(
    record: CardRecord,
    *,
    repo: CardRepository,
    tenant_id: str,
    editor_client: AmendmentEditorClient,
    reviewer_config: ReviewerConfig,
    decision: ReviewerDecision,
    change_request: str,
) -> _AutoEditOutcome:
    """Run the structured-output editor and, on success, splice + route.

    Returns the result for the marker file. The amendment_reviewer's
    caller decides which `AmendmentOutcome` to surface from this.

    Failure modes (each maps to a `fallback_reason`):

    - editor returned None (refusal / SDK failure)
    - editor's confidence is below the project's floor
    - editor named a non-existent ac_index, or the body has no AC block
    - splice failed for any other reason (malformed YAML, dropped index)
    - the transition itself failed
    """
    try:
        ac_items = [item.raw for item in parse_acceptance_block(record.body_md)]
    except Exception as exc:  # noqa: BLE001
        return _AutoEditOutcome(
            applied=False,
            fallback_reason=f"could not parse current AC: {exc}",
            marker_payload={
                "applied": False,
                "reason": f"could not parse current AC: {exc}",
            },
        )
    if not ac_items:
        return _AutoEditOutcome(
            applied=False,
            fallback_reason="card has no acceptance_criteria items to edit",
            marker_payload={
                "applied": False,
                "reason": "card has no acceptance_criteria items to edit",
            },
        )
    try:
        edit = editor_client.edit(
            card_id=record.card_id,
            card_body=record.body_md,
            change_request=change_request,
            ac_items=ac_items,
            reviewer=reviewer_config,
        )
    except Exception as exc:  # noqa: BLE001
        return _AutoEditOutcome(
            applied=False,
            fallback_reason=f"editor client crashed: {exc}",
            marker_payload={
                "applied": False,
                "reason": f"editor client crashed: {exc}",
            },
        )
    if edit is None:
        return _AutoEditOutcome(
            applied=False,
            fallback_reason="editor declined (returned no edit)",
            marker_payload={
                "applied": False,
                "reason": "editor returned no edit",
            },
        )
    # Chunk 6b: attribute the editor's tokens to the card even if the
    # confidence floor or splice gates fail downstream. The model was
    # called either way; the spend is real.
    editor_total_after: int | None = None
    if edit.input_tokens or edit.output_tokens:
        editor_total_after = attribute_to_card(
            repo, record,
            ReviewerUsage(
                input_tokens=edit.input_tokens,
                output_tokens=edit.output_tokens,
                cost_usd=edit.actual_cost_usd or 0.0,
                model_id=edit.model_used or reviewer_config.model_id,
            ),
            tenant_id=tenant_id,
        )
    editor_cost_payload: dict[str, Any] = {
        "input_tokens": edit.input_tokens,
        "output_tokens": edit.output_tokens,
        "actual_cost_usd": (
            round(edit.actual_cost_usd, 6)
            if edit.actual_cost_usd is not None else 0.0
        ),
        "model_used": edit.model_used or reviewer_config.model_id,
        "card_actual_tokens_after": editor_total_after,
    }
    if edit.confidence < reviewer_config.auto_edit_confidence_floor:
        return _AutoEditOutcome(
            applied=False,
            fallback_reason=(
                f"editor confidence {edit.confidence:.2f} below floor "
                f"{reviewer_config.auto_edit_confidence_floor:.2f}"
            ),
            marker_payload={
                "applied": False,
                "reason": "editor confidence below floor",
                "confidence": edit.confidence,
                "floor": reviewer_config.auto_edit_confidence_floor,
                "model_used": edit.model_used,
                "cost": editor_cost_payload,
            },
        )
    timestamp = now_utc_iso()
    try:
        new_body = splice_amendment(
            record.body_md,
            edit,
            reviewer_label=reviewer_config.label,
            timestamp_iso=timestamp,
        )
    except AcEditError as exc:
        return _AutoEditOutcome(
            applied=False,
            fallback_reason=f"splice failed: {exc}",
            marker_payload={
                "applied": False,
                "reason": f"splice failed: {exc}",
                "ac_index": edit.ac_index,
                "model_used": edit.model_used,
                "cost": editor_cost_payload,
            },
        )
    try:
        _route_approve_edited(
            record,
            repo=repo,
            tenant_id=tenant_id,
            reviewer_config=reviewer_config,
            decision=decision,
            edit=edit,
            new_body=new_body,
            timestamp_iso=timestamp,
        )
    except Exception as exc:  # noqa: BLE001
        return _AutoEditOutcome(
            applied=False,
            fallback_reason=f"transition failed: {exc}",
            marker_payload={
                "applied": False,
                "reason": f"transition failed: {exc}",
                "ac_index": edit.ac_index,
                "cost": editor_cost_payload,
            },
        )
    return _AutoEditOutcome(
        applied=True,
        fallback_reason="",
        marker_payload={
            "applied": True,
            "ac_index": edit.ac_index,
            "amendment_reason": edit.amendment_reason,
            "confidence": edit.confidence,
            "model_used": edit.model_used,
            "amended_at": timestamp,
            "cost": editor_cost_payload,
        },
    )


def _route_approve_edited(
    record: CardRecord,
    *,
    repo: CardRepository,
    tenant_id: str,
    reviewer_config: ReviewerConfig,
    decision: ReviewerDecision,
    edit: AmendmentEdit,
    new_body: str,
    timestamp_iso: str,
) -> None:
    """Persist the edited body and transition the card back to `backlog`.

    The contract: "the runner moves the file accordingly. Approve.
    Edit the relevant item ... Set status back to `backlog` or `active`
    per runner choice." We pick `backlog` because the executor's prior
    claim has been cleared (chunk 4 amendment route did that), so the
    next polling tick picks the card up fresh against the amended AC.
    """
    repo.apply_executor_result(
        record.card_id,
        tenant_id=tenant_id,
        body_md=new_body,
        fields=None,
        event=None,
    )
    repo.transition(
        record.card_id,
        to_status=CardStatus.BACKLOG.value,
        tenant_id=tenant_id,
        fields={
            "merge_status": "pending",
            "claimed_by": None,
            "started_at": None,
            "last_heartbeat": None,
            "attempt_trace_id": None,
        },
        actor_id=reviewer_config.label,
        actor_type=ActorType.RUNNER.value,
        event_type=EventType.AMENDED.value,
        payload={
            "source": "amendment_reviewer",
            "outcome": "approved_and_edited",
            "ac_index": edit.ac_index,
            "amendment_reason": edit.amendment_reason,
            "confidence": edit.confidence,
            "model_used": edit.model_used,
            "reviewer_decision_reason": decision.reasoning,
            "amended_at": timestamp_iso,
        },
    )


def _route_deny(
    record: CardRecord,
    *,
    repo: CardRepository,
    tenant_id: str,
    new_body: str,
    reviewer_config: ReviewerConfig,
    decision: ReviewerDecision,
) -> None:
    """Deny path: write the change_request_decision block + go to `active`."""
    try:
        repo.apply_executor_result(
            record.card_id,
            tenant_id=tenant_id,
            body_md=new_body,
            fields=None,
            event=None,
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "amendment_deny body write failed for %s: %s",
            record.card_id, exc,
        )
    try:
        repo.transition(
            record.card_id,
            to_status=CardStatus.ACTIVE.value,
            tenant_id=tenant_id,
            fields={
                # The executor resumes; chunk-4's amendment-route step
                # cleared the claim provenance so the next claim is
                # clean. We leave claim fields alone here -- the next
                # backlog/active tick will pick this back up.
            },
            actor_id=reviewer_config.label,
            actor_type=ActorType.RUNNER.value,
            event_type=EventType.AMENDED.value,
            payload={
                "source": "amendment_reviewer",
                "outcome": "denied",
                "reason": decision.reasoning,
                "confidence": decision.confidence,
                "model_used": decision.model_used,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "amendment_deny transition failed for %s: %s",
            record.card_id, exc,
        )


# ---- change_request parsing -----------------------------------------


_CHANGE_REQUEST_RE = re.compile(
    r"```ya?ml\s*\n(?P<block>change_request:.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def extract_change_request_block(body_md: str) -> str:
    """Return the raw `change_request:` YAML block, or '' if absent.

    The body markdown carries the block fenced as ```yaml in the chunk-4
    executor convention. We return the fenced contents verbatim so the
    reviewer client can read it as YAML or as prose; either works.
    """
    if not body_md:
        return ""
    match = _CHANGE_REQUEST_RE.search(body_md)
    if match:
        return match.group("block")
    # Fallback: a card that wrote `change_request:` without a fence.
    if "change_request:" in body_md:
        idx = body_md.find("change_request:")
        return body_md[idx:]
    return ""


def _append_change_request_decision(
    body_md: str,
    *,
    decision: ReviewerDecision,
    reviewer_config: ReviewerConfig,
    outcome: str,
) -> str:
    """Append a `change_request_decision:` YAML block to the body."""
    lines = [
        "",
        "## Change request decision",
        "",
        "```yaml",
        "change_request_decision:",
        f"  outcome: {outcome}",
        f"  decided_by: {reviewer_config.label}",
        f"  decided_at: {now_utc_iso()}",
        f"  model_used: {decision.model_used or reviewer_config.model_id}",
        f"  confidence: {decision.confidence:.2f}",
        "  reasoning: |",
    ]
    reasoning_text = decision.reasoning or "(no reasoning supplied)"
    for line in reasoning_text.splitlines() or [""]:
        lines.append(f"    {line}")
    lines.append("```")
    lines.append("")
    appendix = "\n".join(lines)
    return (body_md or "").rstrip() + "\n" + appendix + "\n"


# ---- marker io ------------------------------------------------------


def amendment_review_marker_path(paths: RuntimePaths, card_id: str) -> Path:
    return paths.signals / "amendment_reviews" / f"{card_id}.json"


def _write_marker(path: Path, marker: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True), encoding="utf-8")


def _emit_event(
    repo: CardRepository,
    record: CardRecord,
    decision: ReviewerDecision,
    marker: dict[str, Any],
    *,
    tenant_id: str,
) -> None:
    try:
        repo.append_event(
            CardEvent(
                card_id=record.card_id,
                tenant_id=tenant_id,
                type=EventType.AMENDED.value,
                actor_id=marker.get("reviewer_label") or "amendment-reviewer",
                actor_type=ActorType.RUNNER.value,
                at=marker.get("at") or now_utc_iso(),
                payload={
                    "source": "amendment_reviewer",
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "model_used": decision.model_used,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "failed to append amendment-review event for %s: %s",
            record.card_id, exc,
        )


# ---- cost helpers (chunk 6b) ----------------------------------------


def _project_reviewer_cost(
    reviewer_client: SiblingReviewerClient,
    change_request: str,
    card_body: str,
    reviewer_config: ReviewerConfig,
) -> float:
    """Pre-call worst-case USD estimate for the reviewer LLM call."""
    max_tokens = int(getattr(reviewer_client, "max_tokens", 1024))
    est_input = max(1, int((len(change_request) + len(card_body) + 4000) * 1.25 / 4))
    return estimate_call_cost_usd(
        reviewer_config.model_id,
        est_input_tokens=est_input,
        max_output_tokens=max_tokens,
    )


def _project_editor_cost(
    editor_client: AmendmentEditorClient,
    change_request: str,
    card_body: str,
    reviewer_config: ReviewerConfig,
) -> float:
    """Pre-call worst-case USD estimate for the AC editor LLM call."""
    max_tokens = int(getattr(editor_client, "max_tokens", 1024))
    est_input = max(1, int((len(change_request) + len(card_body) + 4000) * 1.25 / 4))
    return estimate_call_cost_usd(
        reviewer_config.model_id,
        est_input_tokens=est_input,
        max_output_tokens=max_tokens,
    )


def _check_amendment_cost_caps(
    *,
    record: CardRecord,
    reviewer_config: ReviewerConfig,
    projected_call_usd: float,
) -> AmendmentOutcome | None:
    """Skip the amendment review when a cap would be breached."""
    if would_exceed_reviewer_cap(
        reviewer_config.cost_cap_usd,
        already_spent_usd=0.0,
        projected_call_usd=projected_call_usd,
    ):
        return AmendmentOutcome(
            card_id=record.card_id,
            action="skipped_cost_cap",
            reason=(
                f"reviewer cost cap ${reviewer_config.cost_cap_usd:.4f} "
                f"would be exceeded by projected ${projected_call_usd:.4f}"
            ),
        )
    breached, cap, total = would_exceed_card_cap(
        record,
        projected_call_usd=projected_call_usd,
        model_id_hint=reviewer_config.model_id,
    )
    if breached and cap is not None:
        return AmendmentOutcome(
            card_id=record.card_id,
            action="skipped_cost_cap",
            reason=(
                f"card cost_cap_usd ${cap:.4f} would be exceeded by "
                f"projected total ${total:.4f}"
            ),
        )
    return None


def _usage_marker_payload(
    decision: ReviewerDecision, card_total_after: int | None,
) -> dict[str, Any]:
    """Compact reviewer-call cost summary for the marker JSON."""
    if decision.usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "actual_cost_usd": 0.0,
            "card_actual_tokens_after": card_total_after,
        }
    return {
        "input_tokens": decision.usage.input_tokens,
        "output_tokens": decision.usage.output_tokens,
        "actual_cost_usd": round(decision.usage.cost_usd, 6),
        "card_actual_tokens_after": card_total_after,
        "model_used": decision.usage.model_id,
    }

