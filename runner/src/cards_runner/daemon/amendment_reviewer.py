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
from .sibling_reviewer import ReviewerDecision, SiblingReviewerClient


log = logging.getLogger(__name__)


AmendmentAction = Literal[
    "reviewed_approve",
    "reviewed_deny",
    "reviewed_comment",
    "skipped_existing",
    "skipped_no_change_request",
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
    tenant_id: str = DEFAULT_TENANT,
) -> list[AmendmentOutcome]:
    """Process `amendments` cards once per tick."""
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

    decision = reviewer_client.review(
        card_id=record.card_id,
        card_body=record.body_md,
        pr_diff=change_request,  # the reviewer reads the change request as 'diff'.
        reviewer=reviewer_config,
    )

    marker = {
        "card_id": record.card_id,
        "decision": decision.decision,
        "reasoning": decision.reasoning,
        "confidence": decision.confidence,
        "model_used": decision.model_used or reviewer_config.model_id,
        "reviewer_label": reviewer_config.label,
        "at": now_utc_iso(),
        "change_request_present": True,
    }
    _write_marker(marker_path, marker)
    _emit_event(repo, record, decision, marker, tenant_id=tenant_id)

    if decision.decision == "approve":
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


