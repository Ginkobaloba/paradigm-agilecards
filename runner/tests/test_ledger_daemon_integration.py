"""Integration: the daemon's executor-exit hook feeds the ledger.

Proves the chunk-2 writer is wired into a real daemon lifecycle hook
(`_post_worker_exit`) behind the off-by-default `ledger_enabled` flag,
and that with the flag ON a clean worker exit produces a `card_metrics`
row rebuilt from the event log. With the flag OFF (the default), no
metrics row and no event log appear -- the daemon behaves exactly as
before, which is what keeps the rest of the suite green.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cards_runner.common.types import (
    EXIT_CLEAN,
    PROJECTED_CARD_NAME,
    WORKER_RESULT_NAME,
    ClaimedCard,
    DaemonConfig,
    RuntimePaths,
)
from cards_runner.daemon.daemon import Daemon, _WorkerHandle
from cards_runner.metrics import events as ev
from cards_runner.metrics.store import MetricsStore
from cards_runner.store.projection import project_card_file
from cards_runner.store.sqlite_store import SqliteRepository


def _cfg(todo_root: Path, store_spec: str, *, ledger_enabled: bool) -> DaemonConfig:
    return DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        poll_interval_sec=0.1,
        skip_worktree=True,
        verifier_enabled=False,  # keep the test focused on the exit hook.
        ledger_enabled=ledger_enabled,
    )


def _claim_and_project(
    repo: SqliteRepository, paths: RuntimePaths, card_id: str,
    *, finished_at: str | None = None, tokens: int | None = None,
) -> ClaimedCard:
    """Claim the card and project it the way a finished worker would.

    The worker stamps `finished_at` / `actual_tokens` into the projected
    file; the non-verbatim projection omits null fields, so we set them
    on the in-memory record before projecting rather than string-editing
    a `null` line that the projection never wrote."""
    attempt = "att-" + card_id
    repo.claim_card(card_id, claimed_by="tester", attempt_trace_id=attempt)
    run_dir = paths.runs / attempt
    run_dir.mkdir(parents=True, exist_ok=True)
    card_file = run_dir / PROJECTED_CARD_NAME
    record = repo.get_card(card_id)
    assert record is not None
    if finished_at is not None:
        record.finished_at = finished_at
    if tokens is not None:
        record.actual_tokens = tokens
    project_card_file(record, card_file, verbatim=False)
    return ClaimedCard(
        card_id=card_id, attempt_trace_id=attempt, trace_id=attempt,
        run_dir=run_dir, worktree_path=run_dir / "worktree",
        card_file=card_file,
    )


def _handle(claim: ClaimedCard) -> _WorkerHandle:
    return _WorkerHandle(claim=claim, process=object(), spawned_at=0.0)  # type: ignore[arg-type]


def test_exit_hook_writes_card_metrics_when_enabled(
    repo: SqliteRepository, paths: RuntimePaths, store_spec: str,
    todo_root: Path, card_factory: Any,
) -> None:
    card_id = "bLED-01"
    card_factory(card_id)
    repo.update_card_fields(card_id, {"work_type": "feature"})
    claim = _claim_and_project(repo, paths, card_id,
                               finished_at="2026-06-01T00:10:00Z", tokens=750)
    (claim.run_dir / WORKER_RESULT_NAME).write_text(
        json.dumps({"exit_code": 0, "actual_cost_usd": 0.2}),
        encoding="utf-8",
    )

    daemon = Daemon(_cfg(todo_root, store_spec, ledger_enabled=True), repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    store = MetricsStore.from_repository(repo)
    row = store.get_card_metrics(tenant_id="default", card_id=card_id)
    assert row is not None
    assert row.work_type == "feature"
    assert row.tier == 2  # points from the test card template
    assert row.agent_attempts == 1
    assert row.executor_tokens_total == 750
    assert row.finished_at == "2026-06-01T00:10:00Z"
    assert row.executor_cost_usd == 0.2
    assert row.incomplete_metrics is False
    # The event log exists and the row is reconstructable from it.
    assert ev.events_path(paths).exists()
    logged = ev.read_events_for_card(paths, card_id=card_id, tenant_id="default")
    kinds = {e.kind for e in logged}
    assert ev.KIND_CARD_CREATED in kinds
    assert ev.KIND_EXECUTOR_EXITED in kinds


def test_verifier_and_merge_gate_telemetry(
    repo: SqliteRepository, paths: RuntimePaths, store_spec: str,
    todo_root: Path, card_factory: Any,
) -> None:
    """A FAIL verdict records one rework cycle; the merge-gate decision
    and PR-open land on the row. Drives the two daemon helpers directly
    (the verifier/gate apply paths need gh + project wiring; the helpers
    are the unit under test for the ledger telemetry)."""
    from cards_runner.daemon.merge_gate import MergeOutcome
    from cards_runner.verifier.runner import VERDICT_FAIL, VerifierResult

    card_id = "bLED-03"
    card_factory(card_id)
    repo.update_card_fields(card_id, {"work_type": "feature"})
    claim = _claim_and_project(repo, paths, card_id,
                               finished_at="2026-06-03T00:10:00Z", tokens=300)
    daemon = Daemon(_cfg(todo_root, store_spec, ledger_enabled=True), repo=repo)
    # Executor exit first (creates the row with work_type/tier).
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)
    # Verifier FAIL -> one rework cycle.
    daemon._record_verifier_metrics(
        claim, VerifierResult(overall_status=VERDICT_FAIL, items=())
    )
    # Merge gate routes to sibling_review and opens a PR.
    daemon._record_merge_gate_metrics(
        claim,
        MergeOutcome(
            decision="sibling_review", to_status="blocked",
            merge_status="requires_review", reason="tier 3-4",
            pr_url="https://github.com/x/y/pull/1",
        ),
    )

    store = MetricsStore.from_repository(repo)
    row = store.get_card_metrics(tenant_id="default", card_id=card_id)
    assert row is not None
    assert row.rework_cycles == 1
    assert row.merge_gate == "sibling_review"
    # The PR-open event landed (pr_opened_at is not a stored column; it
    # only feeds human_review_wall on merge, so assert via the log).
    logged = ev.read_events_for_card(paths, card_id=card_id, tenant_id="default")
    assert any(
        e.kind == ev.KIND_PR_OPENED and e.dedup_key.startswith("opened:")
        for e in logged
    )


def test_skipped_merge_outcome_records_no_gate(
    repo: SqliteRepository, paths: RuntimePaths, store_spec: str,
    todo_root: Path, card_factory: Any,
) -> None:
    from cards_runner.daemon.merge_gate import MergeOutcome
    from cards_runner.verifier.runner import VERDICT_PASS, VerifierResult

    card_id = "bLED-04"
    card_factory(card_id)
    repo.update_card_fields(card_id, {"work_type": "feature"})
    claim = _claim_and_project(repo, paths, card_id,
                               finished_at="2026-06-03T00:10:00Z", tokens=100)
    daemon = Daemon(_cfg(todo_root, store_spec, ledger_enabled=True), repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)
    daemon._record_verifier_metrics(
        claim, VerifierResult(overall_status=VERDICT_PASS, items=())
    )
    daemon._record_merge_gate_metrics(
        claim,
        MergeOutcome(decision="auto", to_status="active",
                     merge_status="pending", reason="gate off", skipped=True),
    )
    store = MetricsStore.from_repository(repo)
    row = store.get_card_metrics(tenant_id="default", card_id=card_id)
    assert row is not None
    assert row.rework_cycles == 0  # verifier ran, no fail
    assert row.merge_gate is None  # skipped gate records nothing


def test_pr_merged_metrics_records_diff_and_wall(
    repo: SqliteRepository, paths: RuntimePaths, store_spec: str,
    todo_root: Path, card_factory: Any,
) -> None:
    """A merged PR records merged_at + diff stats, and the writer derives
    human_review_wall_seconds by pairing the pr-opened event (from the
    merge-gate hook) with merged_at."""
    from cards_runner.daemon.merge_gate import MergeOutcome
    from cards_runner.daemon.unblocker import UnblockDecision

    card_id = "bLED-06"
    card_factory(card_id)
    repo.update_card_fields(card_id, {"work_type": "feature"})
    claim = _claim_and_project(repo, paths, card_id,
                               finished_at="2026-06-03T00:10:00Z", tokens=200)
    daemon = Daemon(_cfg(todo_root, store_spec, ledger_enabled=True), repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)
    # Gate opens a PR (records pr_opened at ~now).
    daemon._record_merge_gate_metrics(
        claim,
        MergeOutcome(decision="human_review", to_status="blocked",
                     merge_status="open", reason="tier 5-6",
                     pr_url="https://github.com/x/y/pull/1"),
    )
    # PR later merges (future merged_at so the wall is positive).
    daemon._record_pr_merged_metrics(UnblockDecision(
        card_id=card_id, action="unblocked",
        merged_at="2026-06-30T00:00:00Z",
        diff_lines_added=120, diff_lines_removed=8,
    ))
    store = MetricsStore.from_repository(repo)
    row = store.get_card_metrics(tenant_id="default", card_id=card_id)
    assert row is not None
    assert row.merged_at == "2026-06-30T00:00:00Z"
    assert row.diff_lines_added == 120
    assert row.diff_lines_removed == 8
    assert row.human_review_wall_seconds is not None
    assert row.human_review_wall_seconds > 0


def test_verifier_and_gate_helpers_noop_when_disabled(
    repo: SqliteRepository, paths: RuntimePaths, store_spec: str,
    todo_root: Path, card_factory: Any,
) -> None:
    """With ledger off, the verifier/gate helpers write nothing."""
    from cards_runner.daemon.merge_gate import MergeOutcome
    from cards_runner.verifier.runner import VERDICT_FAIL, VerifierResult

    card_id = "bLED-05"
    card_factory(card_id)
    claim = _claim_and_project(repo, paths, card_id)
    daemon = Daemon(_cfg(todo_root, store_spec, ledger_enabled=False), repo=repo)
    daemon._record_verifier_metrics(
        claim, VerifierResult(overall_status=VERDICT_FAIL, items=())
    )
    daemon._record_merge_gate_metrics(
        claim,
        MergeOutcome(decision="sibling_review", to_status="blocked",
                     merge_status="requires_review", reason="x",
                     pr_url="https://x/y/1"),
    )
    store = MetricsStore.from_repository(repo)
    assert store.get_card_metrics(tenant_id="default", card_id=card_id) is None
    assert not ev.events_path(paths).exists()


def test_exit_hook_is_noop_when_disabled(
    repo: SqliteRepository, paths: RuntimePaths, store_spec: str,
    todo_root: Path, card_factory: Any,
) -> None:
    card_id = "bLED-02"
    card_factory(card_id)
    repo.update_card_fields(card_id, {"work_type": "feature"})
    claim = _claim_and_project(repo, paths, card_id,
                               finished_at="2026-06-01T00:10:00Z", tokens=750)

    daemon = Daemon(_cfg(todo_root, store_spec, ledger_enabled=False), repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    store = MetricsStore.from_repository(repo)
    assert store.get_card_metrics(tenant_id="default", card_id=card_id) is None
    assert not ev.events_path(paths).exists()
