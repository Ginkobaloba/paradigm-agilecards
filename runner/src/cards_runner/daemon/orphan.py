"""Orphan reclaim.

A card whose `last_heartbeat` is older than the project's
`orphan_timeout_minutes` is treated as orphaned. The daemon moves it
back to `backlog/`, clears the claim metadata, and leaves the
worktree intact for forensics. The reaper (chunk 4) deletes
worktrees older than the forensic TTL.

Per RUNNER_CONTRACT.md "Heartbeat and orphan reclaim" the runner
preserves `cascade_history` and `verifier_cascade_history` across
reclaim. Chunk 1 clears only the four claim fields plus
`attempt_trace_id`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..common.atomic import atomic_move
from ..common.card_io import parse_card_file, write_card_file
from ..common.types import (
    CardSnapshot,
    DaemonConfig,
    RuntimePaths,
    SUBFOLDER_BACKLOG,
    parse_iso,
)


log = logging.getLogger(__name__)


def scan_for_orphans(
    *,
    paths: RuntimePaths,
    cfg: DaemonConfig,
    now: datetime | None = None,
) -> list[Path]:
    """Return paths of cards in active/ that look orphaned.

    A card is orphaned when its `last_heartbeat` is older than
    `cfg.orphan_timeout_sec`. Missing `last_heartbeat` is also
    treated as orphaned (the worker never wrote its first
    heartbeat), since we have no positive evidence of life.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    cutoff_sec = cfg.orphan_timeout_sec
    out: list[Path] = []
    if not paths.active.is_dir():
        return out
    for entry in paths.active.iterdir():
        if not entry.is_file() or entry.suffix != ".md":
            continue
        if _is_orphan(entry, now=now, cutoff_sec=cutoff_sec):
            out.append(entry)
    return out


def _is_orphan(card_path: Path, *, now: datetime, cutoff_sec: int) -> bool:
    try:
        snap = parse_card_file(card_path)
    except Exception as exc:
        log.warning("could not parse %s during orphan scan: %s", card_path, exc)
        return False
    last_hb = snap.get("last_heartbeat")
    if last_hb is None:
        # Card claimed but never heartbeated. Compare against
        # `started_at` instead; if that is older than cutoff, it is
        # orphaned. This handles the daemon-crash-between-move-and-stamp
        # case too because the boot path re-stamps started_at.
        started = snap.get("started_at")
        ref = parse_iso(started) if started else None
    else:
        ref = parse_iso(str(last_hb))
    if ref is None:
        # No timestamps at all; treat as malformed-claim, not orphan.
        return False
    age_sec = (now - ref).total_seconds()
    return age_sec > cutoff_sec


def reclaim(card_path: Path, *, paths: RuntimePaths) -> Path:
    """Move a card from active/ back to backlog/. Returns the new path.

    Clears `claimed_by`, `started_at`, `last_heartbeat`, and
    `attempt_trace_id`. Preserves `cascade_history` and every other
    field the planner or prior runs wrote.

    Idempotent: if the card has already been reclaimed (file is now
    in backlog/) we return the existing path without raising.
    """
    backlog_path = paths.backlog / card_path.name
    if not card_path.exists():
        if backlog_path.exists():
            return backlog_path
        raise FileNotFoundError(card_path)

    snap = parse_card_file(card_path)
    _clear_claim_fields(snap)
    write_card_file(card_path, snap)
    atomic_move(card_path, backlog_path)
    log.info(
        "reclaimed orphan card_id=%s from active/ -> backlog/",
        snap.card_id,
    )
    return backlog_path


def force_reclaim(card_id: str, *, paths: RuntimePaths) -> Path:
    """Reclaim a card by id, regardless of heartbeat.

    Looks for the card in `active/`. Raises FileNotFoundError if it
    is not there. CLI surface (`cards-runner reclaim`) hits this.
    """
    candidates = [
        p for p in _iter_active_cards(paths)
        if _matches_card_id(p, card_id)
    ]
    if not candidates:
        raise FileNotFoundError(f"no card with id {card_id} in active/")
    if len(candidates) > 1:
        raise RuntimeError(
            f"multiple cards in active/ match id {card_id}: {candidates}"
        )
    return reclaim(candidates[0], paths=paths)


def _iter_active_cards(paths: RuntimePaths) -> Iterable[Path]:
    if not paths.active.is_dir():
        return iter(())
    return (
        p for p in paths.active.iterdir()
        if p.is_file() and p.suffix == ".md"
    )


def _matches_card_id(path: Path, card_id: str) -> bool:
    if path.stem == card_id:
        return True
    try:
        snap = parse_card_file(path)
    except Exception:
        return False
    return snap.card_id == card_id


def _clear_claim_fields(snap: CardSnapshot) -> None:
    snap.frontmatter["status"] = SUBFOLDER_BACKLOG
    snap.frontmatter["claimed_by"] = None
    snap.frontmatter["started_at"] = None
    snap.frontmatter["last_heartbeat"] = None
    snap.frontmatter["attempt_trace_id"] = None
