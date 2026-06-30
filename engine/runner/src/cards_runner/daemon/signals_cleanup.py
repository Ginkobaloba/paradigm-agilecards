"""Cleanup sweep for the per-card reviewer marker dirs.

Chunk 5 wrote per-card markers under `signals/sibling_reviews/<id>.json`
and `signals/amendment_reviews/<id>.json` to prevent re-reviewing a
PR or change_request the reviewer already decided on. The chunk-4
forensic reaper handles `_runs/`, not `signals/`; old markers accumulate
indefinitely.

This module is the chunk 6d sweep. Each tick (after the forensic
reaper) it walks both signals subdirs and deletes a marker when:

1. The marker is older than `cfg.reviewer_marker_ttl_hours` (default
   72 hours -- long enough to debug a Friday-afternoon issue on Monday
   morning), AND
2. The card the marker names has reached a terminal state (`done`,
   `blocked`) OR no card row exists for the marker (orphan).

Important: this sweep does NOT touch `signals/reviewer_history.jsonl`
or any other historical record. The per-card markers are a runtime
de-dup mechanism (a marker means "we already reviewed this; skip");
the historical record lives separately and is append-only forever.

A removal failure is logged but never fatal -- the next tick retries.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from ..common.types import DaemonConfig, RuntimePaths
from ..store import DEFAULT_TENANT, CardRepository, CardStatus


log = logging.getLogger(__name__)


# Subdirs the sweep walks. The card id is encoded in the filename stem
# (`<card_id>.json`) so we can pair markers back to card rows quickly.
_MARKER_SUBDIRS: tuple[str, ...] = ("sibling_reviews", "amendment_reviews")


# Statuses where the marker is safe to delete: the card has reached a
# terminal state and the marker no longer serves a de-dup purpose.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {CardStatus.DONE.value, CardStatus.BLOCKED.value}
)


@dataclass(frozen=True)
class CleanupDecision:
    """One marker's cleanup decision. Returned for tests + tick summary."""

    path: Path
    subdir: str           # "sibling_reviews" or "amendment_reviews"
    card_id: str
    action: str           # "removed", "kept_recent", "kept_active",
                           # "kept_unreadable", "removed_orphan"
    reason: str = ""


def sweep_reviewer_markers(
    *,
    repo: CardRepository,
    cfg: DaemonConfig,
    paths: RuntimePaths,
    tenant_id: str = DEFAULT_TENANT,
    now: float | None = None,
) -> list[CleanupDecision]:
    """Walk the signals dirs and delete eligible markers.

    Pure-on-the-store-side: only reads `query_cards` once to build an
    id -> status index. The TTL is short-circuit-disabled when
    `reviewer_marker_ttl_hours <= 0`.
    """
    ttl_hours = cfg.reviewer_marker_ttl_hours
    if ttl_hours <= 0:
        log.debug("reviewer_marker_ttl_hours=%s; signals sweep disabled", ttl_hours)
        return []
    ttl_seconds = float(ttl_hours) * 3600.0
    now_ts = now if now is not None else time.time()

    status_index: dict[str, str] = {
        record.card_id: record.status
        for record in repo.query_cards(tenant_id=tenant_id)
    }

    decisions: list[CleanupDecision] = []
    for subdir_name in _MARKER_SUBDIRS:
        subdir = paths.signals / subdir_name
        if not subdir.is_dir():
            continue
        for marker in _iter_markers(subdir):
            decision = _decide_marker(
                marker, subdir_name=subdir_name,
                status_index=status_index, now_ts=now_ts,
                ttl_seconds=ttl_seconds,
            )
            decisions.append(decision)
    return decisions


def _iter_markers(subdir: Path) -> Iterator[Path]:
    try:
        for child in subdir.iterdir():
            if child.is_file() and child.suffix == ".json":
                yield child
    except OSError as exc:
        log.warning("could not list %s: %s", subdir, exc)


def _decide_marker(
    marker: Path,
    *,
    subdir_name: str,
    status_index: dict[str, str],
    now_ts: float,
    ttl_seconds: float,
) -> CleanupDecision:
    card_id = marker.stem
    try:
        mtime = marker.stat().st_mtime
    except OSError as exc:
        return CleanupDecision(
            path=marker, subdir=subdir_name, card_id=card_id,
            action="kept_unreadable", reason=f"stat failed: {exc}",
        )
    age = now_ts - mtime
    if age < ttl_seconds:
        return CleanupDecision(
            path=marker, subdir=subdir_name, card_id=card_id,
            action="kept_recent",
            reason=f"age {age / 3600:.1f}h < ttl {ttl_seconds / 3600:.1f}h",
        )
    status = status_index.get(card_id)
    if status is None:
        # The card row vanished (migration / explicit delete). The marker
        # is an orphan; remove it.
        ok = _remove_marker(marker)
        return CleanupDecision(
            path=marker, subdir=subdir_name, card_id=card_id,
            action="removed_orphan" if ok else "kept_unreadable",
            reason=(
                "card row absent; orphan marker reaped"
                if ok else "remove failed; retry next tick"
            ),
        )
    if status not in _TERMINAL_STATUSES:
        return CleanupDecision(
            path=marker, subdir=subdir_name, card_id=card_id,
            action="kept_active",
            reason=f"card still {status}",
        )
    ok = _remove_marker(marker)
    return CleanupDecision(
        path=marker, subdir=subdir_name, card_id=card_id,
        action="removed" if ok else "kept_unreadable",
        reason=(
            f"card terminal ({status}); marker age {age / 3600:.1f}h"
            if ok else "remove failed; retry next tick"
        ),
    )


def _remove_marker(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError as exc:
        log.warning("could not remove marker %s: %s", path, exc)
        return False


def split_decisions(
    decisions: Iterable[CleanupDecision],
) -> dict[str, list[CleanupDecision]]:
    """Group decisions by `action` for the tick summary."""
    buckets: dict[str, list[CleanupDecision]] = {}
    for d in decisions:
        buckets.setdefault(d.action, []).append(d)
    return buckets
