"""Orphan reclaim, store-backed.

A card whose `last_heartbeat` is older than the project's
`orphan_timeout_minutes` is treated as orphaned: its worker is
presumed dead. The runner transitions it back to `backlog` in the
card store, clears the claim metadata, and appends a `reclaimed`
event. The worktree is left intact for forensics; the reaper
(chunk 4) deletes worktrees older than the forensic TTL.

This is the chunk 2b cutover of v1's filesystem orphan path. v1
scanned the `active/` subfolder and `os.replace`'d files back to
`backlog/`. The card store is now canonical, so the scan is a
`query_cards(status="active")` and the reclaim is a `transition`.
The heartbeat the scan reads is the store column the daemon mirrors
each tick from the live worker; an orphan is simply a card the
daemon stopped being able to refresh.

Per RUNNER_CONTRACT.md "Heartbeat and orphan reclaim" the runner
preserves `cascade_history` and `verifier_cascade_history` across a
reclaim. `transition` only touches `status` and the four claim
fields, so every other field is carried forward untouched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..common.types import DaemonConfig, parse_iso
from ..store import (
    DEFAULT_TENANT,
    ActorType,
    CardRecord,
    CardRepository,
    CardStatus,
    EventType,
)


log = logging.getLogger(__name__)


# The frontmatter mutation a reclaim applies: status returns to
# backlog and the four claim fields are cleared so the next claim
# starts from a clean slate.
_RECLAIM_FIELDS: dict[str, None] = {
    "claimed_by": None,
    "started_at": None,
    "last_heartbeat": None,
    "attempt_trace_id": None,
}


def scan_for_orphans(
    *,
    repo: CardRepository,
    cfg: DaemonConfig,
    tenant_id: str = DEFAULT_TENANT,
    now: datetime | None = None,
) -> list[str]:
    """Return the card ids of `active` cards that look orphaned.

    A card is orphaned when its `last_heartbeat` is older than
    `cfg.orphan_timeout_sec`. A missing `last_heartbeat` falls back to
    `started_at`; a card with neither timestamp is treated as
    malformed rather than orphaned and is skipped (it cannot happen
    under the transactional claim, which stamps both atomically, but
    the guard is cheap).
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    cutoff_sec = cfg.orphan_timeout_sec
    out: list[str] = []
    for record in repo.query_cards(
        tenant_id=tenant_id, status=CardStatus.ACTIVE.value
    ):
        if _is_orphan(record, now=now, cutoff_sec=cutoff_sec):
            out.append(record.card_id)
    return out


def _is_orphan(record: CardRecord, *, now: datetime, cutoff_sec: int) -> bool:
    ref_text = record.last_heartbeat or record.started_at
    if ref_text is None:
        return False
    try:
        ref = parse_iso(ref_text)
    except ValueError:
        log.warning(
            "card %s has unparseable heartbeat/started_at %r; skipping",
            record.card_id, ref_text,
        )
        return False
    if ref is None:
        return False
    age_sec = (now - ref).total_seconds()
    return age_sec > cutoff_sec


def reclaim(
    repo: CardRepository,
    card_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT,
) -> CardRecord:
    """Transition a card from `active` back to `backlog` in the store.

    Clears the four claim fields and appends a `reclaimed` event.
    Every planner- or run-owned field (including `cascade_history`)
    is preserved. Returns the updated `CardRecord`.
    """
    updated = repo.transition(
        card_id,
        to_status=CardStatus.BACKLOG.value,
        tenant_id=tenant_id,
        fields=dict(_RECLAIM_FIELDS),
        actor_type=ActorType.RUNNER.value,
        event_type=EventType.RECLAIMED.value,
    )
    log.info("reclaimed card_id=%s active -> backlog", card_id)
    return updated


def force_reclaim(
    repo: CardRepository,
    card_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT,
) -> CardRecord:
    """Reclaim a card by id regardless of heartbeat.

    The CLI surface (`cards-runner reclaim`) hits this. Raises
    `FileNotFoundError` if the id does not exist; raises `RuntimeError`
    if the card is not currently `active` (there is nothing to
    reclaim). The exception split matches the CLI's existing exit
    codes (3 for not-found, 1 for the runtime error).
    """
    record = repo.get_card(card_id, tenant_id=tenant_id)
    if record is None:
        raise FileNotFoundError(f"no card with id {card_id}")
    if record.status != CardStatus.ACTIVE.value:
        raise RuntimeError(
            f"card {card_id} is {record.status!r}, not active; "
            "nothing to reclaim"
        )
    return reclaim(repo, card_id, tenant_id=tenant_id)
