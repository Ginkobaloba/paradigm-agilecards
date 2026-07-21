"""Tier-aware merge gate.

RUNNER_CONTRACT.md "Merge gates":

  - tier 1, 2: auto-merge if `lint && tests && !conflicts`
  - tier 3, 4: auto-merge after a sibling-agent reviewer says ok
  - tier 5, 6: open PR, wait for Drew's approval
  - `pin_required: true` (set from stakes=high) overrides any
    per-project relaxation. High-stakes cards always go through human
    review.

Chunk 4 implements the data flow. The auto-merge path (tier 1-2,
non-pinned, no pin override) pushes the per-card branch, opens a PR
via gh, and runs `gh pr merge --auto`; on success the card transitions
to `done` with `merge_status=merged`. The sibling and human review
gates push the branch and open the PR but do NOT merge -- the card
transitions to `blocked` with `merge_status=requires_review` (sibling)
or `merge_status=open` (human). RUNNER_CONTRACT.md describes `blocked`
as "cards finished but unmerged, or paused on a dependency"; an
awaiting-merge card is in the first category. Chunk 5 will add the
unblock side (poll `gh pr view` for the merge to land externally).

When `DaemonConfig.pr_gate_enabled` is False the gate degrades to the
chunk-3 behavior: it returns the auto-merge decision with `merged`
status (skipping the actual gh calls). This keeps the verifier-routing
test suite working without GitHub.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from ..common.canonical_config import TierMap, load_tier_map
from ..common.project_config import MergeGateRelaxation, ProjectConfig
from ..common.types import ClaimedCard, DaemonConfig
from ..store import CardRecord, CardStatus
from .pr_lifecycle import GhRunner, NullGhRunner
from .worktree import attempt_branch_name


log = logging.getLogger(__name__)


Decision = Literal["auto", "sibling_review", "human_review"]


# Merge status enum values per RUNNER_CONTRACT.md.
_MS_MERGED = "merged"
_MS_OPEN = "open"
_MS_REQUIRES_REVIEW = "requires_review"
_MS_CONFLICT = "conflict"
_MS_BLOCKED = "blocked"


@dataclass(frozen=True)
class MergeOutcome:
    """The merge gate's decision rendered into a state transition.

    The daemon's `_verifier_apply_pass` consumes this: it stamps the
    verifier provenance fields and then applies `to_status`,
    `merge_status`, and the event payload exactly as the outcome
    describes. Splitting decision from application keeps the gate
    pure-enough to unit-test against a fake gh runner.
    """

    decision: Decision
    to_status: str
    merge_status: str
    reason: str
    pr_url: str | None = None
    extra_payload: dict[str, Any] | None = None
    skipped: bool = False  # True when the gate degraded to a no-op.


def decide_gate(
    record: CardRecord,
    *,
    tier_map: TierMap | None = None,
    relaxation: MergeGateRelaxation | None = None,
) -> Decision:
    """Pick the gate purely from card frontmatter.

    Decision order:

    1. `pin_required=True` on the card OR on the canonical tier map for
       the card's `points`. Routes to `human_review`.
    2. Otherwise route by `points`:
       1-2 -> `auto`, 3-4 -> `sibling_review`, 5-6 -> `human_review`.
    3. Chunk 5: a project may opt tier 3-4 cards into auto-merge by
       setting `merge_gate.auto_merge_tier_3_4: true` in its
       `project.yaml`. The pin override still wins.

    The pin override always wins -- the contract is explicit:
    "pin_required: true (set from stakes=high) overrides any
    per-project relaxation".
    """
    tmap = tier_map if tier_map is not None else load_tier_map()
    points = _safe_int(record.field_value("points"), default=2)
    pin_required_card = _truthy(record.field_value("pin_required"))
    pin_required_tier = tmap.pin_required_for(points)
    if pin_required_card or pin_required_tier:
        return "human_review"
    if points <= 2:
        return "auto"
    if points <= 4:
        if relaxation is not None and relaxation.auto_merge_tier_3_4:
            return "auto"
        return "sibling_review"
    return "human_review"


@dataclass
class MergeGate:
    """The gate the daemon dispatches a verified card through.

    `gh` is injectable; tests pass a fake. `pr_gate_enabled=False`
    short-circuits to the chunk-3 transition (done, merge_status=merged)
    so chunk-3-era tests keep their semantics unchanged.

    Chunk 5: callers pass the live `ProjectConfig` into `apply()` so a
    mid-run project.yaml reload takes effect immediately. The daemon
    reads it from `ProjectConfigLoader.current()` each tick.
    """

    cfg: DaemonConfig
    gh: GhRunner
    tier_map: TierMap | None = None

    def apply(
        self,
        claim: ClaimedCard,
        record: CardRecord,
        *,
        verified_at: str,
        project_config: ProjectConfig | None = None,
    ) -> MergeOutcome:
        relaxation = (
            project_config.merge_gate if project_config else None
        )
        decision = decide_gate(
            record, tier_map=self.tier_map, relaxation=relaxation
        )

        if not self.cfg.pr_gate_enabled:
            # PR gate not wired (the default until a project opts in).
            # Preserve chunk-3 behavior: the card lands `done` with
            # `merge_status=merged` regardless of tier.
            return MergeOutcome(
                decision=decision,
                to_status=CardStatus.DONE.value,
                merge_status=_MS_MERGED,
                reason="pr_gate_disabled; verifier pass -> done (chunk 3 behavior)",
                skipped=True,
            )

        # The working branch is per-attempt (see `attempt_branch_name`): it
        # MUST match the branch `prepare_worktree` created for this claim, or
        # the push targets a branch that does not exist. Derive it the same
        # way, from the same attempt id, not from the card's fixed `branch:`
        # field alone.
        base_branch_name = str(
            record.field_value("branch") or f"card/{record.card_id}"
        )
        branch = attempt_branch_name(base_branch_name, claim.attempt_trace_id)
        # Base branch precedence: card frontmatter > project.yaml > daemon
        # default. The card-level field wins so a one-off (e.g., a
        # back-port to a release branch) is still possible without
        # editing project.yaml.
        base_override = (
            relaxation.pr_base_branch if relaxation is not None else None
        )
        base = str(
            record.field_value("base_branch")
            or base_override
            or self.cfg.pr_base_branch_default
        )
        title = self._pr_title(record)
        body = self._pr_body(record, decision=decision, verified_at=verified_at)

        # Push the per-card branch. Failure here halts the gate; the
        # card lands `blocked` with merge_status=blocked and the gh
        # reason in the payload.
        push_result = self.gh.push(claim.worktree_path, branch)
        if not push_result.ok:
            return MergeOutcome(
                decision=decision,
                to_status=CardStatus.BLOCKED.value,
                merge_status=_MS_BLOCKED,
                reason=f"push failed: {push_result.reason}",
                extra_payload={
                    "gh_call": "push",
                    "stderr_tail": (push_result.stderr or "")[-200:],
                },
            )

        # Open the PR. A failure here is also fatal for this attempt.
        open_result = self.gh.open_pr(
            claim.worktree_path,
            title=title,
            body=body,
            base=base,
            draft=False,
        )
        if not open_result.ok:
            return MergeOutcome(
                decision=decision,
                to_status=CardStatus.BLOCKED.value,
                merge_status=_MS_BLOCKED,
                reason=f"gh pr create failed: {open_result.reason}",
                extra_payload={
                    "gh_call": "pr_create",
                    "stderr_tail": (open_result.stderr or "")[-200:],
                },
            )
        pr_url = (open_result.parsed or {}).get("pr_url") or ""

        if decision == "auto":
            # Tier 1-2 path: ask gh to auto-merge once the gate (CI, no
            # conflicts) clears. On Drew's local repo this typically
            # lands immediately; on a repo with CI it lands when CI
            # passes. Either way the card transitions to `done` here
            # because the runner's decision has been made; if the merge
            # later conflicts, gh closes the PR and a human handles it.
            merge_result = self.gh.merge_pr(
                claim.worktree_path,
                identifier=pr_url or branch,
                strategy=self.cfg.auto_merge_strategy,
            )
            if not merge_result.ok:
                # A conflict-driven merge failure is the most likely
                # branch here. Route to blocked with merge_status=conflict
                # so the operator knows the kind of fix needed.
                ms = _MS_CONFLICT if "conflict" in merge_result.reason.lower() else _MS_BLOCKED
                return MergeOutcome(
                    decision=decision,
                    to_status=CardStatus.BLOCKED.value,
                    merge_status=ms,
                    reason=f"gh pr merge failed: {merge_result.reason}",
                    pr_url=pr_url or None,
                    extra_payload={
                        "gh_call": "pr_merge",
                        "stderr_tail": (merge_result.stderr or "")[-200:],
                    },
                )
            return MergeOutcome(
                decision=decision,
                to_status=CardStatus.DONE.value,
                merge_status=_MS_MERGED,
                reason="auto-merge gate cleared",
                pr_url=pr_url or None,
            )

        if decision == "sibling_review":
            return MergeOutcome(
                decision=decision,
                to_status=CardStatus.BLOCKED.value,
                merge_status=_MS_REQUIRES_REVIEW,
                reason="awaiting sibling-agent review (tier 3-4)",
                pr_url=pr_url or None,
            )

        # human_review.
        return MergeOutcome(
            decision=decision,
            to_status=CardStatus.BLOCKED.value,
            merge_status=_MS_OPEN,
            reason="awaiting human merge (tier 5-6 or pin_required)",
            pr_url=pr_url or None,
        )

    @staticmethod
    def _pr_title(record: CardRecord) -> str:
        title = record.title or record.card_id
        return f"{record.card_id}: {title}"

    @staticmethod
    def _pr_body(
        record: CardRecord, *, decision: Decision, verified_at: str
    ) -> str:
        """A short, deterministic PR body. Carries the trace id and gate."""
        lines = [
            f"agile-cards card `{record.card_id}` cleared the cold-read verifier.",
            "",
            f"- trace_id: `{record.trace_id or 'n/a'}`",
            f"- points: {record.points} / stakes: {record.stakes or 'n/a'}",
            f"- merge gate: {decision}",
            f"- verified_at: {verified_at}",
        ]
        if record.batch:
            lines.append(f"- batch: {record.batch}")
        lines.append("")
        lines.append("This PR was opened by the cards-runner merge gate.")
        return "\n".join(lines)


def build_default_gh_runner(cfg: DaemonConfig) -> GhRunner:
    """Pick the right gh wrapper from the daemon config.

    Off (`pr_gate_enabled=False`) returns the null runner so even
    accidental gh calls during a refactor land a clear error.
    """
    if not cfg.pr_gate_enabled:
        return NullGhRunner()
    # Imported here to keep the daemon's import surface small when the
    # PR gate is off (the common case in tests).
    from .pr_lifecycle import SubprocessGhRunner

    return SubprocessGhRunner(
        gh_path=cfg.gh_path,
        git_path=cfg.git_path,
    )


# ---- helpers ---------------------------------------------------------


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "on"}
    return False
