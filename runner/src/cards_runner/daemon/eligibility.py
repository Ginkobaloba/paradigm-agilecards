"""Claim eligibility for backlog cards.

RUNNER_CONTRACT.md "Claim protocol" pegs the eligibility decision on
three things:

1. Every entry in the card's `depends_on` must be in `done` with
   `merge_status: merged`.
2. If the project sets `story_source_path`, the file's current sha256
   must match the card's `story_hash`. A mismatch routes the card to
   `blocked` for re-triage.
3. `requires_pre_approval: true` cards may not be claimed without an
   explicit human approval the runner can verify (`signals_dir/
   preapproval/<card_id>.ok` is the marker chunk 4 picks; the contract
   leaves the mechanism to the runner).

The daemon's poll loop calls `evaluate_eligibility` for every backlog
card per tick. The return value carries one of three actions:

- `claim` -- the card is good to attempt a claim on this tick.
- `skip`  -- not claimable right now, but transient (a dependency is
  not yet merged, a pre-approval marker missing). The card stays in
  backlog; the next tick re-checks.
- `block` -- a hard failure (story drift, the only one today). The
  daemon transitions the card to `blocked` so future ticks do not
  re-read the source file uselessly.

This module is pure: it reads the store and the filesystem but never
writes. The daemon owns every transition. That separation matters for
tests, which can drive `evaluate_eligibility` directly against a SQLite
store without needing the rest of the daemon scaffolding.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..common.project_config import ProjectConfig
from ..common.types import DaemonConfig, RuntimePaths
from ..store import DEFAULT_TENANT, CardRecord, CardRepository, CardStatus


log = logging.getLogger(__name__)


Action = Literal["claim", "skip", "block"]


@dataclass(frozen=True)
class EligibilityResult:
    """The decision `evaluate_eligibility` returns for one card.

    `kind` is a short tag for telemetry: `dependency`, `story_drift`,
    `pre_approval`, `ok`. `reason` is a human-readable sentence the
    daemon logs and (for `block`) writes into the lifecycle event
    payload. `detail` carries machine-readable specifics (the list of
    unmerged dependency card ids, the computed and expected hash, etc.)
    so the dashboard can render the failure without re-deriving it.
    """

    action: Action
    kind: str
    reason: str = ""
    detail: dict[str, Any] | None = None


def evaluate_eligibility(
    record: CardRecord,
    *,
    repo: CardRepository,
    cfg: DaemonConfig,
    paths: RuntimePaths,
    tenant_id: str = DEFAULT_TENANT,
    project_config: ProjectConfig | None = None,
) -> EligibilityResult:
    """Apply the contract's three checks. Returns the resulting action.

    The order matters: pre-approval is checked first because a card
    that isn't approved should not even compute dependency state; story
    drift is checked next because a drifted card needs to be routed to
    blocked regardless of dependency state; dependencies are checked
    last because they are the most common skip reason and the cheapest
    to re-evaluate next tick.

    Chunk 5: `project_config` is now consulted as the fallback story
    source path when the card frontmatter omits it. The contract calls
    this out ("If the project config sets `story_source_path`, the
    runner ... compares against the card's `story_hash`"); chunk 4 only
    read the per-card field.
    """
    approval = _check_pre_approval(record, paths=paths)
    if approval is not None:
        return approval

    drift = _check_story_drift(record, cfg=cfg, project_config=project_config)
    if drift is not None:
        return drift

    deps = _check_dependencies(record, repo=repo, tenant_id=tenant_id)
    if deps is not None:
        return deps

    return EligibilityResult(action="claim", kind="ok")


# ---- pre-approval ----------------------------------------------------


def _check_pre_approval(
    record: CardRecord, *, paths: RuntimePaths
) -> EligibilityResult | None:
    requires = record.field_value("requires_pre_approval")
    if not _truthy(requires):
        return None
    marker = paths.signals / "preapproval" / f"{record.card_id}.ok"
    if marker.is_file():
        return None
    return EligibilityResult(
        action="skip",
        kind="pre_approval",
        reason=f"awaiting pre-approval marker at {marker}",
        detail={"marker_path": str(marker)},
    )


# ---- story drift -----------------------------------------------------


def _check_story_drift(
    record: CardRecord,
    *,
    cfg: DaemonConfig,
    project_config: ProjectConfig | None = None,
) -> EligibilityResult | None:
    source_path = _resolve_story_source_path(record, cfg, project_config)
    if source_path is None:
        return None
    declared_hash = record.field_value("story_hash")
    if not declared_hash:
        # The planner did not stamp a hash; nothing to compare against.
        return None
    try:
        actual_hash = _sha256_of_file(source_path)
    except OSError as exc:
        log.warning(
            "card %s story_source_path %s is unreadable (%s); "
            "treating as skip rather than blocked drift",
            record.card_id, source_path, exc,
        )
        return EligibilityResult(
            action="skip",
            kind="story_drift",
            reason=f"story source path unreadable: {source_path} ({exc})",
            detail={"source_path": str(source_path)},
        )
    if actual_hash == str(declared_hash).lower():
        return None
    return EligibilityResult(
        action="block",
        kind="story_drift",
        reason=(
            f"story drift: declared {declared_hash} but source "
            f"{source_path} now hashes to {actual_hash}"
        ),
        detail={
            "source_path": str(source_path),
            "declared_hash": str(declared_hash),
            "actual_hash": actual_hash,
        },
    )


def _resolve_story_source_path(
    record: CardRecord,
    cfg: DaemonConfig,
    project_config: ProjectConfig | None,
) -> Path | None:
    """Resolve where the card's source story lives, if anywhere.

    Precedence:

    1. `card.story_source_path` in the frontmatter (planner stamped).
    2. `project_config.story_source_path` (chunk 5 fallback for cards
       whose planner did not stamp the field).
    3. None.

    Relative paths are resolved against the card's `project` when set;
    that keeps a `docs/source.md` style entry working without absolute
    paths in either the card or the project config.
    """
    raw = record.field_value("story_source_path")
    if not raw and project_config is not None:
        raw = project_config.story_source_path
    if not raw:
        del cfg  # reserved for further future per-project maps.
        return None
    try:
        path = Path(str(raw)).expanduser()
    except Exception:  # noqa: BLE001
        return None
    if not path.is_absolute() and record.project:
        path = Path(record.project) / path
    return path


def _sha256_of_file(path: Path) -> str:
    """sha256 hex of a file's bytes. Used for story-drift comparison."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- dependencies ----------------------------------------------------


def _check_dependencies(
    record: CardRecord,
    *,
    repo: CardRepository,
    tenant_id: str,
) -> EligibilityResult | None:
    dep_ids = repo.get_dependencies(record.card_id, tenant_id=tenant_id)
    if not dep_ids:
        return None
    unmet: list[dict[str, Any]] = []
    for dep_id in dep_ids:
        dep = repo.get_card(dep_id, tenant_id=tenant_id)
        if dep is None:
            unmet.append({"id": dep_id, "reason": "not found"})
            continue
        if dep.status != CardStatus.DONE.value:
            unmet.append({"id": dep_id, "reason": f"status={dep.status}"})
            continue
        # RUNNER_CONTRACT.md "Claim protocol" #1: a dependency in
        # `done/` also needs `merge_status: merged`. A done-but-unmerged
        # dependency is not actually integrated work.
        if dep.merge_status != "merged":
            unmet.append({
                "id": dep_id,
                "reason": f"merge_status={dep.merge_status or 'null'}",
            })
    if not unmet:
        return None
    return EligibilityResult(
        action="skip",
        kind="dependency",
        reason=f"{len(unmet)} unmet dependency edge(s)",
        detail={"unmet": unmet},
    )


# ---- helpers ---------------------------------------------------------


def _truthy(value: Any) -> bool:
    """YAML-ish truthiness for `requires_pre_approval` and friends."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "on"}
    return False
