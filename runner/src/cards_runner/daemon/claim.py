"""Claim protocol: backlog -> active with frontmatter stamp.

The atomic move (os.replace) is the arbitration primitive. Two
daemons or two pollers racing the same card both call os.replace;
exactly one succeeds, the rest get FileNotFoundError. The winner
then writes the frontmatter.

This decoupling matters: the move is what makes the claim atomic.
We do NOT lock the card file first and then move; that would race
the move with another claimant who saw the file in backlog/ before
our lock landed.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from ..common.atomic import atomic_move
from ..common.card_io import parse_card_file, write_card_file
from ..common.types import (
    CardSnapshot,
    ClaimedCard,
    RuntimePaths,
    SUBFOLDER_ACTIVE,
    now_utc_iso,
)


log = logging.getLogger(__name__)


# Windows + AV can briefly hold a file just after a rename. We retry
# the post-move parse a few times before giving up. The deadline is
# tiny on purpose: a real PermissionError that lasts longer than this
# is a real problem we want to surface.
_POST_MOVE_PARSE_RETRIES = 5
_POST_MOVE_PARSE_BACKOFF_SEC = 0.02


class ClaimRace(Exception):
    """Raised when the atomic move fails because another claimant won."""


def attempt_claim(
    backlog_card: Path,
    *,
    paths: RuntimePaths,
    claimed_by: str,
) -> ClaimedCard:
    """Try to claim a card. Raises `ClaimRace` if we lost.

    On success the card is in `active/` with `status: active`,
    `claimed_by`, `started_at`, `last_heartbeat`, and
    `attempt_trace_id` stamped. The worktree path is allocated but
    NOT created here; worktree creation is the next step and uses
    the global mutex.

    The card file is written AFTER the move. Once the move succeeds
    the card is OURS; we do NOT roll back on a subsequent parse
    failure (that would re-expose the file to other claimants and
    can trigger a ping-pong loop under heavy contention). A parse
    failure after a successful claim is reported as an exception
    and the card stays in `active/` so the boot reconcile path can
    repair it on the next daemon start.
    """
    if not backlog_card.is_file():
        raise ClaimRace(f"{backlog_card} disappeared before claim")
    dest = paths.active / backlog_card.name
    try:
        atomic_move(backlog_card, dest)
    except FileNotFoundError as exc:
        raise ClaimRace(
            f"lost race on {backlog_card.name}: {exc}"
        ) from exc
    except OSError as exc:
        # Windows can raise PermissionError if a sibling daemon's
        # rename is in flight. Treat it the same as a lost race.
        raise ClaimRace(
            f"could not move {backlog_card.name} into active/: {exc}"
        ) from exc

    attempt_trace_id = str(uuid.uuid4())
    worktree_path = paths.runs / attempt_trace_id / "worktree"

    snapshot = _parse_with_retry(dest)

    now = now_utc_iso()
    snapshot.frontmatter["status"] = SUBFOLDER_ACTIVE
    snapshot.frontmatter["claimed_by"] = claimed_by
    snapshot.frontmatter["started_at"] = now
    snapshot.frontmatter["last_heartbeat"] = now
    snapshot.frontmatter["attempt_trace_id"] = attempt_trace_id
    write_card_file(dest, snapshot)
    log.info(
        "claimed card_id=%s attempt=%s -> %s",
        snapshot.card_id, attempt_trace_id, dest,
    )

    return ClaimedCard(
        card_id=snapshot.card_id,
        active_path=dest,
        attempt_trace_id=attempt_trace_id,
        worktree_path=worktree_path,
        snapshot=snapshot,
    )


def _parse_with_retry(path: Path) -> CardSnapshot:
    """Parse a card file with a small retry budget for transient OS errors.

    On Windows, antivirus and indexers occasionally hold a file
    briefly after a rename. The retry budget is intentionally small;
    a real persistent failure should surface as the original error
    so the daemon's boot reconciler can flag the card.
    """
    last_exc: Exception | None = None
    for attempt in range(_POST_MOVE_PARSE_RETRIES):
        try:
            return parse_card_file(path)
        except (PermissionError, FileNotFoundError) as exc:
            last_exc = exc
            time.sleep(_POST_MOVE_PARSE_BACKOFF_SEC * (attempt + 1))
    assert last_exc is not None
    raise last_exc
