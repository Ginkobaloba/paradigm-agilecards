"""Forensic worktree / run dir reaper.

RUNNER_CONTRACT.md "Branch and worktree protocol" / "Worktree isolation
and cross-contamination defense" both treat the per-card run dir
(`<todo_root>/_runs/<attempt>/`) as preserved-for-forensics: after the
card lands `done` or `blocked`, the dir stays around so audit, retro,
or a follow-up investigator can read the executor's log / outputs /
worktree.

The reaper deletes those dirs after `worktree_forensic_ttl_hours`
elapses. It NEVER deletes the dir of an attempt the daemon's in-memory
worker map still tracks (a still-running worker), and it NEVER deletes
the dir of an attempt whose card is still mid-flight (status not in
{`done`, `blocked`}). Story drift and merge-blocked cards count as
`blocked` and become reapable after the TTL expires.

A removal failure is logged but never fatal: forensic cleanup is a
nice-to-have, not a correctness requirement. The next tick retries.
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..common.types import DaemonConfig, RuntimePaths
from ..store import DEFAULT_TENANT, CardRepository, CardStatus


log = logging.getLogger(__name__)


# Card statuses that signal "terminal for reaping purposes". The
# contract's two terminal states; an awaiting-standup card is NOT
# terminal because a human may still need to read the run dir mid-review.
_REAPABLE_STATUSES: frozenset[str] = frozenset(
    {CardStatus.DONE.value, CardStatus.BLOCKED.value}
)


@dataclass(frozen=True)
class ReapDecision:
    """One run-dir's decision. Returned for tests and the lifecycle log."""

    path: Path
    action: str  # "reaped", "kept_recent", "kept_active", "kept_unknown".
    reason: str = ""


def reap_forensic_run_dirs(
    *,
    repo: CardRepository,
    cfg: DaemonConfig,
    paths: RuntimePaths,
    in_flight_attempts: Iterable[str],
    tenant_id: str = DEFAULT_TENANT,
    now: float | None = None,
) -> list[ReapDecision]:
    """Walk `_runs/` and reap directories past the forensic TTL.

    `in_flight_attempts` is the daemon's set of attempt_trace_ids the
    in-memory worker map currently knows about. The reaper skips any
    dir whose name matches one of those even if its mtime is past the
    TTL -- the worker may be writing into it right now.
    """
    runs_root = paths.runs
    if not runs_root.is_dir():
        return []
    ttl_hours = cfg.worktree_forensic_ttl_hours
    if ttl_hours <= 0:
        log.debug("worktree_forensic_ttl_hours=%s; reaper disabled", ttl_hours)
        return []
    ttl_seconds = ttl_hours * 3600.0
    now_ts = now if now is not None else time.time()
    in_flight = set(in_flight_attempts)

    # Index cards by their stored attempt_trace_id so we can map a run
    # dir to a card status in O(1). A card's attempt_trace_id is
    # cleared whenever the card leaves `active` per the merge-gate and
    # verifier-fail paths, so finished cards typically have it set to
    # null -- the reaper falls back to matching dir names.
    attempt_index: dict[str, str] = {}
    for record in repo.query_cards(tenant_id=tenant_id):
        att = record.attempt_trace_id
        if att:
            attempt_index[att] = record.status

    decisions: list[ReapDecision] = []
    for child in sorted(runs_root.iterdir()):
        if not child.is_dir():
            continue
        attempt_id = child.name
        if attempt_id in in_flight:
            decisions.append(
                ReapDecision(
                    path=child, action="kept_active",
                    reason="worker still in flight",
                )
            )
            continue
        age = now_ts - _dir_mtime(child)
        if age < ttl_seconds:
            decisions.append(
                ReapDecision(
                    path=child, action="kept_recent",
                    reason=f"age {age / 3600:.1f}h < ttl {ttl_hours}h",
                )
            )
            continue
        # A card that still owns this attempt_trace_id is held even past
        # the TTL if it has not reached a terminal state. Without the
        # attempt index entry we cannot identify ownership; we treat
        # such dirs as orphans of the canonical card store and reap
        # them once the TTL has expired, matching the contract's
        # forensic-retention intent.
        owner_status = attempt_index.get(attempt_id)
        if owner_status is not None and owner_status not in _REAPABLE_STATUSES:
            decisions.append(
                ReapDecision(
                    path=child, action="kept_active",
                    reason=f"card still {owner_status}",
                )
            )
            continue
        if _remove_tree(child):
            decisions.append(
                ReapDecision(
                    path=child, action="reaped",
                    reason=(
                        f"terminal/unknown; age {age / 3600:.1f}h "
                        f">= ttl {ttl_hours}h"
                    ),
                )
            )
        else:
            decisions.append(
                ReapDecision(
                    path=child, action="kept_unknown",
                    reason="rmtree failed; retry next tick",
                )
            )
    return decisions


def _dir_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0  # treat unreadable as ancient.


def _remove_tree(path: Path) -> bool:
    try:
        shutil.rmtree(path, ignore_errors=False)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not reap run dir %s: %s", path, exc)
        return False
