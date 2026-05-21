"""Poll-for-merged unblocker (chunk 5).

The chunk-4 merge gate parks awaiting-merge cards in `blocked` with
`merge_status in {open, requires_review}` after opening the PR. The
runner cannot keep them parked forever: once the PR is merged externally
(a human merged it, a sibling reviewer auto-merged via gh's `--auto`
flag, GitHub's branch protection finally cleared the gate), the card
needs to progress to `done` with `merge_status=merged`.

This module is the daemon-tick sub-task that drives that progression.
Each tick it:

1. Queries the store for `blocked` cards whose `merge_status` is one of
   the awaiting-merge values.
2. For each, reads the `pr_url` column (chunk-5 promoted from event
   payload). A row without a URL is skipped with a debug log -- the
   merge gate stores the URL on success; absence means the gate ran
   before the chunk-5 column existed (legacy data) or the URL was lost
   to a failed write.
3. Calls `gh pr view --json state,mergedAt` via the existing GhRunner.
4. When `state == "MERGED"`, transitions the card to `done` with
   `merge_status=merged` and emits a `merged` event recording the gh
   `mergedAt` timestamp.

The contract motivates this as the "unblock side" the chunk-4 handoff
named as deferred. A sibling reviewer (chunk 5 too) writes an approval
marker, then `gh pr merge --auto` lets the PR through; this module
catches the merge and converts the `blocked` state back to `done`.

`pr_unblock_enabled=False` by default -- a daemon that hasn't wired gh
must not start spawning gh subprocesses just because it has blocked
cards lying around. The CLI flag `--pr-unblock` flips it on; production
runs typically set both `--pr-gate` and `--pr-unblock`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable

from ..common.types import DaemonConfig, now_utc_iso
from ..store import (
    DEFAULT_TENANT,
    ActorType,
    CardRecord,
    CardRepository,
    CardStatus,
    EventType,
)
from .pr_lifecycle import GhRunner


log = logging.getLogger(__name__)


# Awaiting-merge merge_status values. The contract names two:
# `open` (human review pending) and `requires_review` (sibling pending).
# `conflict` is not unblockable from gh state alone; a human fixes a
# merge conflict before the PR can merge.
_AWAITING_MERGE: frozenset[str] = frozenset({"open", "requires_review"})


@dataclass(frozen=True)
class UnblockDecision:
    """One blocked card's unblock decision. Returned for tests and logs.

    `action` is one of:

    - `unblocked` -- transitioned to done/merged.
    - `still_pending` -- gh reported the PR is not merged yet.
    - `skipped_no_url` -- the row has no pr_url (legacy or missing).
    - `skipped_gh_failure` -- gh returned non-zero; the card stays put.
    - `skipped_unknown_state` -- gh returned a state we did not expect.
    """

    card_id: str
    action: str
    reason: str = ""
    pr_state: str | None = None
    merged_at: str | None = None


def unblock_merged_cards(
    *,
    repo: CardRepository,
    gh: GhRunner,
    cfg: DaemonConfig,
    actor_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> list[UnblockDecision]:
    """Walk blocked cards with awaiting-merge status; promote merged ones.

    Pure on the gh side (`view_pr` only), write-on-success on the store
    side. A daemon disabled via `cfg.pr_unblock_enabled=False` is a no-op
    and returns the empty list. The decision list is returned for the
    daemon's tick summary and for unit tests.
    """
    if not cfg.pr_unblock_enabled:
        return []
    decisions: list[UnblockDecision] = []
    blocked = repo.query_cards(
        tenant_id=tenant_id, status=CardStatus.BLOCKED.value
    )
    for record in blocked:
        if record.merge_status not in _AWAITING_MERGE:
            continue
        decision = _process_card(
            record, repo=repo, gh=gh, actor_id=actor_id, tenant_id=tenant_id
        )
        decisions.append(decision)
    return decisions


def _process_card(
    record: CardRecord,
    *,
    repo: CardRepository,
    gh: GhRunner,
    actor_id: str | None,
    tenant_id: str,
) -> UnblockDecision:
    pr_url = (record.pr_url or "").strip()
    if not pr_url:
        log.debug(
            "card %s has no pr_url; cannot poll for merge", record.card_id
        )
        return UnblockDecision(
            card_id=record.card_id,
            action="skipped_no_url",
            reason="no pr_url on card row",
        )
    view = gh.view_pr(identifier=pr_url)
    if not view.ok:
        log.info(
            "gh pr view failed for %s (%s): %s",
            record.card_id, pr_url, view.reason,
        )
        return UnblockDecision(
            card_id=record.card_id,
            action="skipped_gh_failure",
            reason=view.reason,
        )
    parsed = view.parsed or {}
    state = _normalize_state(parsed.get("state"))
    merged_at = parsed.get("mergedAt") or parsed.get("merged_at")
    if state != "merged":
        return UnblockDecision(
            card_id=record.card_id,
            action="still_pending",
            reason=f"gh state={state or 'unknown'!r}",
            pr_state=state,
            merged_at=merged_at,
        )
    if not _transition_to_done(
        record,
        repo=repo,
        actor_id=actor_id,
        tenant_id=tenant_id,
        pr_url=pr_url,
        merged_at=merged_at,
    ):
        return UnblockDecision(
            card_id=record.card_id,
            action="skipped_gh_failure",
            reason="store transition failed",
            pr_state=state,
            merged_at=merged_at,
        )
    return UnblockDecision(
        card_id=record.card_id,
        action="unblocked",
        reason="gh reported MERGED; transitioned blocked -> done",
        pr_state=state,
        merged_at=merged_at,
    )


def _transition_to_done(
    record: CardRecord,
    *,
    repo: CardRepository,
    actor_id: str | None,
    tenant_id: str,
    pr_url: str,
    merged_at: str | None,
) -> bool:
    payload: dict[str, Any] = {
        "pr_url": pr_url,
        "merge_status": "merged",
        "trigger": "pr_unblock",
    }
    if merged_at:
        payload["merged_at"] = merged_at
    try:
        repo.transition(
            record.card_id,
            to_status=CardStatus.DONE.value,
            tenant_id=tenant_id,
            fields={"merge_status": "merged"},
            actor_id=actor_id or f"cards-runner-unblocker@pid{os.getpid()}",
            actor_type=ActorType.RUNNER.value,
            event_type=EventType.MERGED.value,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("unblock transition failed for %s: %s", record.card_id, exc)
        return False
    log.info(
        "card %s unblocked: PR %s merged at %s",
        record.card_id, pr_url, merged_at or now_utc_iso(),
    )
    return True


def _normalize_state(value: Any) -> str | None:
    """gh's JSON has historically returned `state` in mixed case.

    `--json state` returns "OPEN" / "CLOSED" / "MERGED" in gh 2.x;
    older versions may emit lower-case. Normalize so the comparison is
    a single equality check.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def split_decisions(
    decisions: Iterable[UnblockDecision],
) -> dict[str, list[UnblockDecision]]:
    """Group decisions by `action` for the daemon tick summary."""
    buckets: dict[str, list[UnblockDecision]] = {}
    for d in decisions:
        buckets.setdefault(d.action, []).append(d)
    return buckets
