"""The card_metrics writer (ledger chunk 2).

This is the write side of `docs/design/throughput_metrics_ledger.md`.
The chunk-1 schema created `card_metrics`; the chunk-3 estimator reads
it; this module is what actually puts data in it.

Design (spec sections 5.2 / 5.4):

- **Authoritative log, denormalized row.** Every lifecycle transition
  appends a `MetricsEvent` to `signals/metrics_events.jsonl`
  (authoritative) and then rebuilds the card's `card_metrics` row by
  folding that card's entire event stream (`fold_events`). The row is a
  cache of the fold; it can always be reconstructed from the log, which
  is exactly what the section-12.3 replay verification asserts.

- **Idempotent under replay.** Cumulative fields (`agent_attempts`,
  `executor_tokens_total`, `rework_cycles`, ...) are computed by folding
  deduplicated events keyed on `(kind, dedup_key)`, NOT by incrementing
  the previous row. So a crash that re-processes a worker exit and
  appends its event twice does not double-count: the fold collapses the
  two identical `(kind, dedup_key)` events into one. This sidesteps the
  chunk-6b stale-read trampling bug from the start.

- **Best-effort.** Every public method swallows its own failures (logs
  at WARNING, returns False). A metrics write must never abort the
  daemon sweep that called it.

The high-level `record_*` methods are the surface the daemon lifecycle
hooks call. `fold_events` is a pure function exposed for testing and for
the replay verification.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ..common.types import RuntimePaths, now_utc_iso, parse_iso
from . import events as ev
from .store import CardMetricsFullRow, MetricsStore

log = logging.getLogger(__name__)


def fold_events(events: Iterable[ev.MetricsEvent]) -> CardMetricsFullRow | None:
    """Rebuild a `card_metrics` row from one card's event stream.

    Pure and deterministic: same events (in any order, with any
    duplicates) produce the same row. Returns None for an empty stream.

    Accumulating kinds are deduplicated by `(kind, dedup_key)` before
    being summed/counted; last-wins kinds take the latest value in file
    order; set kinds (regression ids) collapse to a sorted unique tuple.
    """
    materialized = list(events)
    if not materialized:
        return None

    card_id = materialized[0].card_id
    tenant_id = materialized[0].tenant_id

    work_type: str | None = None
    tier: int | None = None
    pin_required: bool | None = None
    contract_authored_at: str | None = None
    started_at: str | None = None
    merge_gate: str | None = None
    merged_at: str | None = None
    pr_opened_at: str | None = None
    diff_added: int | None = None
    diff_removed: int | None = None
    human_review_wall: float | None = None
    contract_survived: bool | None = None
    incomplete_flag = False

    # Accumulating kinds: dedupe by dedup_key, keep the last payload seen.
    exec_attempts: dict[str, dict[str, Any]] = {}
    verifier_runs: dict[str, dict[str, Any]] = {}
    reviewer_calls: dict[str, dict[str, Any]] = {}
    rework_keys: set[str] = set()
    regression_ids: set[str] = set()

    for event in materialized:
        payload = event.payload
        if payload.get("incomplete"):
            incomplete_flag = True

        if event.kind == ev.KIND_CARD_CREATED:
            if payload.get("work_type") is not None:
                work_type = str(payload["work_type"])
            if payload.get("tier") is not None:
                tier = int(payload["tier"])
            if payload.get("pin_required") is not None:
                pin_required = bool(payload["pin_required"])
            if payload.get("contract_authored_at") is not None:
                contract_authored_at = str(payload["contract_authored_at"])
        elif event.kind == ev.KIND_CARD_STARTED:
            value = payload.get("started_at")
            if value is not None:
                text = str(value)
                if started_at is None or text < started_at:
                    started_at = text
        elif event.kind == ev.KIND_EXECUTOR_EXITED:
            exec_attempts[event.dedup_key] = payload
        elif event.kind == ev.KIND_VERIFIER_DECIDED:
            verifier_runs[event.dedup_key] = payload
        elif event.kind == ev.KIND_REWORK_TRIGGERED:
            rework_keys.add(event.dedup_key)
        elif event.kind == ev.KIND_REVIEWER_SPEND:
            reviewer_calls[event.dedup_key] = payload
        elif event.kind == ev.KIND_PR_OPENED:
            # One kind carries two last-wins scalars on distinct dedup
            # keys: the PR-open timestamp and the merge-gate decision.
            if payload.get("pr_opened_at") is not None:
                pr_opened_at = str(payload["pr_opened_at"])
            if payload.get("merge_gate") is not None:
                merge_gate = str(payload["merge_gate"])
        elif event.kind == ev.KIND_PR_MERGED:
            if payload.get("merged_at") is not None:
                merged_at = str(payload["merged_at"])
            if payload.get("diff_lines_added") is not None:
                diff_added = int(payload["diff_lines_added"])
            if payload.get("diff_lines_removed") is not None:
                diff_removed = int(payload["diff_lines_removed"])
            if payload.get("human_review_wall_seconds") is not None:
                human_review_wall = float(payload["human_review_wall_seconds"])
        elif event.kind == ev.KIND_REGRESSION_FLAGGED:
            regression_ids.add(event.dedup_key)
        elif event.kind == ev.KIND_CONTRACT_OUTCOME:
            if payload.get("contract_survived") is not None:
                contract_survived = bool(payload["contract_survived"])

    agent_attempts = len(exec_attempts) or None
    agent_wall_seconds: float | None = (
        sum(_as_float(p.get("wall_seconds")) for p in exec_attempts.values())
        if exec_attempts else None
    )
    executor_tokens_total: int | None = (
        sum(_as_int(p.get("tokens")) for p in exec_attempts.values())
        if exec_attempts else None
    )
    executor_cost_usd: float | None = (
        sum(_as_float(p.get("cost_usd")) for p in exec_attempts.values())
        if any(p.get("cost_usd") is not None for p in exec_attempts.values())
        else None
    )
    verifier_tokens_total: int | None = (
        sum(_as_int(p.get("tokens")) for p in verifier_runs.values())
        if verifier_runs else None
    )
    reviewer_tokens_total: int | None = (
        sum(_as_int(p.get("tokens")) for p in reviewer_calls.values())
        if reviewer_calls else None
    )
    rework_cycles: int | None = (
        len(rework_keys) if (rework_keys or verifier_runs) else None
    )

    finished_candidates = [
        str(p["finished_at"]) for p in exec_attempts.values()
        if p.get("finished_at") is not None
    ]
    finished_at = max(finished_candidates) if finished_candidates else None

    if human_review_wall is None and pr_opened_at and merged_at:
        human_review_wall = _wall_seconds(pr_opened_at, merged_at)

    incomplete_metrics = incomplete_flag or work_type is None or tier is None

    return CardMetricsFullRow(
        tenant_id=tenant_id,
        card_id=card_id,
        work_type=work_type,
        tier=tier,
        pin_required=pin_required,
        contract_authored_at=contract_authored_at,
        started_at=started_at,
        finished_at=finished_at,
        agent_wall_seconds=agent_wall_seconds,
        agent_attempts=agent_attempts,
        executor_tokens_total=executor_tokens_total,
        executor_cost_usd=executor_cost_usd,
        verifier_tokens_total=verifier_tokens_total,
        reviewer_tokens_total=reviewer_tokens_total,
        human_review_wall_seconds=human_review_wall,
        rework_cycles=rework_cycles,
        diff_lines_added=diff_added,
        diff_lines_removed=diff_removed,
        merge_gate=merge_gate,
        merged_at=merged_at,
        regression_card_ids=tuple(sorted(regression_ids)),
        contract_survived=contract_survived,
        incomplete_metrics=incomplete_metrics,
    )


class LedgerWriter:
    """Append metrics events and keep the `card_metrics` row in sync.

    Construct with the runtime paths (for the JSONL log) and a
    `MetricsStore` (for the row upsert). The daemon builds one of these
    at boot when `ledger_enabled` is set and hands it to the lifecycle
    hooks.
    """

    def __init__(self, paths: RuntimePaths, store: MetricsStore) -> None:
        self._paths = paths
        self._store = store

    # ---- lifecycle hooks ---------------------------------------------

    def record_card_created(
        self,
        *,
        card_id: str,
        tenant_id: str,
        work_type: str | None,
        tier: int | None,
        pin_required: bool | None,
        contract_authored_at: str | None = None,
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_CARD_CREATED, dedup_key=card_id,
            payload={
                "work_type": work_type,
                "tier": tier,
                "pin_required": pin_required,
                "contract_authored_at": contract_authored_at,
                "incomplete": work_type is None or tier is None,
            },
        ))

    def record_card_started(
        self, *, card_id: str, tenant_id: str,
        attempt_trace_id: str, started_at: str,
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_CARD_STARTED, dedup_key=attempt_trace_id,
            payload={"started_at": started_at},
        ))

    def record_executor_exit(
        self,
        *,
        card_id: str,
        tenant_id: str,
        attempt_trace_id: str,
        started_at: str | None,
        finished_at: str | None,
        tokens: int = 0,
        cost_usd: float | None = None,
        wall_seconds: float | None = None,
    ) -> bool:
        """Record one worker exit. `dedup_key` is the attempt trace id so
        a replayed exit for the same attempt does not double-count."""
        if wall_seconds is None and started_at and finished_at:
            wall_seconds = _wall_seconds(started_at, finished_at)
        payload: dict[str, object] = {
            "started_at": started_at,
            "finished_at": finished_at,
            "tokens": int(tokens),
            "wall_seconds": wall_seconds,
        }
        if cost_usd is not None:
            payload["cost_usd"] = float(cost_usd)
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_EXECUTOR_EXITED, dedup_key=attempt_trace_id,
            payload=payload,
        ))

    def record_verifier_decided(
        self,
        *,
        card_id: str,
        tenant_id: str,
        attempt_trace_id: str,
        failed: bool,
        tokens: int = 0,
    ) -> bool:
        """Record a verifier verdict. On FAIL also emits a rework event
        keyed on the same attempt so the rework count is idempotent."""
        ok = self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_VERIFIER_DECIDED, dedup_key=attempt_trace_id,
            payload={"failed": failed, "tokens": int(tokens)},
        ))
        if failed:
            ok = self.record_rework(
                card_id=card_id, tenant_id=tenant_id,
                rework_id=f"verifier:{attempt_trace_id}",
            ) and ok
        return ok

    def record_rework(
        self, *, card_id: str, tenant_id: str, rework_id: str
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_REWORK_TRIGGERED, dedup_key=rework_id,
            payload={},
        ))

    def record_reviewer_spend(
        self, *, card_id: str, tenant_id: str, call_id: str, tokens: int
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_REVIEWER_SPEND, dedup_key=call_id,
            payload={"tokens": int(tokens)},
        ))

    def record_pr_opened(
        self, *, card_id: str, tenant_id: str, pr_opened_at: str
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_PR_OPENED, dedup_key=f"opened:{card_id}",
            payload={"pr_opened_at": pr_opened_at},
        ))

    def record_merge_gate(
        self, *, card_id: str, tenant_id: str, gate: str
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_PR_OPENED, dedup_key=f"gate:{card_id}",
            payload={"merge_gate": gate},
        ))

    def record_pr_merged(
        self,
        *,
        card_id: str,
        tenant_id: str,
        merged_at: str | None,
        diff_lines_added: int | None = None,
        diff_lines_removed: int | None = None,
        human_review_wall_seconds: float | None = None,
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_PR_MERGED, dedup_key=card_id,
            payload={
                "merged_at": merged_at,
                "diff_lines_added": diff_lines_added,
                "diff_lines_removed": diff_lines_removed,
                "human_review_wall_seconds": human_review_wall_seconds,
            },
        ))

    def record_regression(
        self,
        *,
        parent_card_id: str,
        tenant_id: str,
        regressing_card_id: str,
    ) -> bool:
        """Flag that `regressing_card_id` is a bugfix against
        `parent_card_id`. Deduped by the regressing id so the same
        bugfix is only counted once on the parent's row."""
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=parent_card_id, tenant_id=tenant_id,
            kind=ev.KIND_REGRESSION_FLAGGED, dedup_key=regressing_card_id,
            payload={},
        ))

    def record_contract_outcome(
        self, *, card_id: str, tenant_id: str, survived: bool
    ) -> bool:
        return self._emit(ev.MetricsEvent(
            at=now_utc_iso(), card_id=card_id, tenant_id=tenant_id,
            kind=ev.KIND_CONTRACT_OUTCOME, dedup_key=card_id,
            payload={"contract_survived": survived},
        ))

    # ---- rebuild -----------------------------------------------------

    def rebuild_card(self, *, card_id: str, tenant_id: str) -> bool:
        """Recompute and upsert one card's row from its event log.

        Exposed for the section-12.3 replay verification and for a
        one-shot resync. Returns False on failure (best-effort)."""
        try:
            self._rebuild(card_id, tenant_id)
            return True
        except Exception as exc:  # noqa: BLE001 - best-effort by contract.
            log.warning(
                "card_metrics rebuild failed for %s: %s", card_id, exc
            )
            return False

    def _emit(self, event: ev.MetricsEvent) -> bool:
        if not ev.append_event(self._paths, event):
            return False
        return self.rebuild_card(
            card_id=event.card_id, tenant_id=event.tenant_id
        )

    def _rebuild(self, card_id: str, tenant_id: str) -> None:
        stream = ev.read_events_for_card(
            self._paths, card_id=card_id, tenant_id=tenant_id
        )
        row = fold_events(stream)
        if row is not None:
            self._store.upsert_card_metrics(row)
            self._store.commit()


def _wall_seconds(start_iso: str, end_iso: str) -> float | None:
    start = parse_iso(start_iso)
    end = parse_iso(end_iso)
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


__all__ = [
    "LedgerWriter",
    "fold_events",
]
