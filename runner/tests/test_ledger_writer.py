"""Tests for ledger chunk 2: the card_metrics writer.

Covers the three load-bearing guarantees from
`docs/design/throughput_metrics_ledger.md`:

- The event log round-trips and tolerates malformed lines (3.2).
- `fold_events` rebuilds a row deterministically and is idempotent
  under duplicate events -- cumulative fields do not double-count when
  a transition is replayed (5.4).
- The `LedgerWriter` keeps the `card_metrics` row in sync with the log,
  and a row rebuilt purely from the JSONL matches the live row (the
  section-12.3 audit-log replay verification).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cards_runner.common.types import RuntimePaths
from cards_runner.metrics import events as ev
from cards_runner.metrics.store import MetricsStore
from cards_runner.metrics.writer import LedgerWriter, fold_events
from cards_runner.store.sqlite_store import SqliteRepository

TENANT = "default"


@pytest.fixture
def writer(paths: RuntimePaths, store_path: Path):
    """A LedgerWriter backed by a schema-initialized SQLite store and a
    JSONL log under the test's todo root. Yields (writer, store, paths)."""
    repo = SqliteRepository.open(str(store_path))
    store = MetricsStore.from_repository(repo)
    try:
        yield LedgerWriter(paths, store), store, paths
    finally:
        repo.close()


# ---- event log round trip --------------------------------------------


def test_event_append_and_read_round_trip(paths: RuntimePaths) -> None:
    event = ev.MetricsEvent(
        at="2026-06-01T00:00:00Z", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_CARD_CREATED, dedup_key="b1",
        payload={"work_type": "feature", "tier": 3},
    )
    assert ev.append_event(paths, event) is True
    back = ev.read_events(paths)
    assert len(back) == 1
    assert back[0] == event


def test_read_events_skips_malformed_line(paths: RuntimePaths) -> None:
    good = ev.MetricsEvent(
        at="2026-06-01T00:00:00Z", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_CARD_STARTED, dedup_key="t1",
        payload={"started_at": "2026-06-01T00:00:00Z"},
    )
    ev.append_event(paths, good)
    # Corrupt the log with a junk line; the reader must skip it.
    with ev.events_path(paths).open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    back = ev.read_events(paths)
    assert len(back) == 1
    assert back[0].card_id == "b1"


def test_read_events_missing_log_is_empty(paths: RuntimePaths) -> None:
    assert ev.read_events(paths) == []


# ---- fold_events: math + idempotency ---------------------------------


def test_fold_empty_is_none() -> None:
    assert fold_events([]) is None


def test_fold_accumulates_across_distinct_attempts() -> None:
    events = [
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_CARD_CREATED, dedup_key="b1",
                        payload={"work_type": "feature", "tier": 3,
                                 "pin_required": False}),
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_EXECUTOR_EXITED, dedup_key="att-1",
                        payload={"tokens": 100, "wall_seconds": 10.0,
                                 "finished_at": "2026-06-01T00:01:00Z"}),
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_EXECUTOR_EXITED, dedup_key="att-2",
                        payload={"tokens": 50, "wall_seconds": 5.0,
                                 "finished_at": "2026-06-01T00:02:00Z"}),
    ]
    row = fold_events(events)
    assert row is not None
    assert row.agent_attempts == 2
    assert row.executor_tokens_total == 150
    assert row.agent_wall_seconds == 15.0
    assert row.finished_at == "2026-06-01T00:02:00Z"  # latest
    assert row.work_type == "feature"
    assert row.tier == 3
    assert row.incomplete_metrics is False


def test_fold_is_idempotent_under_duplicate_event() -> None:
    """The same attempt's exit appended twice (crash replay) must NOT
    double-count tokens or attempts -- spec 5.4."""
    exit_event = ev.MetricsEvent(
        at="t", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_EXECUTOR_EXITED, dedup_key="att-1",
        payload={"tokens": 100, "wall_seconds": 10.0},
    )
    created = ev.MetricsEvent(
        at="t", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_CARD_CREATED, dedup_key="b1",
        payload={"work_type": "feature", "tier": 3},
    )
    once = fold_events([created, exit_event])
    twice = fold_events([created, exit_event, exit_event])
    assert once == twice
    assert twice is not None
    assert twice.agent_attempts == 1
    assert twice.executor_tokens_total == 100


def test_fold_verifier_fail_counts_one_rework_idempotently() -> None:
    created = ev.MetricsEvent(
        at="t", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_CARD_CREATED, dedup_key="b1",
        payload={"work_type": "bugfix", "tier": 2})
    fail = ev.MetricsEvent(
        at="t", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_REWORK_TRIGGERED, dedup_key="verifier:att-1",
        payload={})
    verdict = ev.MetricsEvent(
        at="t", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_VERIFIER_DECIDED, dedup_key="att-1",
        payload={"failed": True, "tokens": 20})
    row = fold_events([created, verdict, fail, verdict, fail])
    assert row is not None
    assert row.rework_cycles == 1
    assert row.verifier_tokens_total == 20


def test_fold_regression_ids_dedupe_and_sort() -> None:
    base = dict(at="t", card_id="parent", tenant_id=TENANT,
                kind=ev.KIND_REGRESSION_FLAGGED, payload={})
    events = [
        ev.MetricsEvent(dedup_key="bugfix-2", **base),
        ev.MetricsEvent(dedup_key="bugfix-1", **base),
        ev.MetricsEvent(dedup_key="bugfix-2", **base),  # dup
    ]
    row = fold_events(events)
    assert row is not None
    assert row.regression_card_ids == ("bugfix-1", "bugfix-2")


def test_fold_human_review_wall_from_open_and_merge() -> None:
    events = [
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_CARD_CREATED, dedup_key="b1",
                        payload={"work_type": "feature", "tier": 5}),
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_PR_OPENED, dedup_key="opened:b1",
                        payload={"pr_opened_at": "2026-06-01T00:00:00Z"}),
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_PR_MERGED, dedup_key="b1",
                        payload={"merged_at": "2026-06-01T01:00:00Z",
                                 "diff_lines_added": 40,
                                 "diff_lines_removed": 5}),
    ]
    row = fold_events(events)
    assert row is not None
    assert row.human_review_wall_seconds == 3600.0
    assert row.diff_lines_added == 40
    assert row.merged_at == "2026-06-01T01:00:00Z"


def test_fold_tolerates_corrupt_event() -> None:
    """A JSON-valid but garbage event (non-numeric tier / tokens / junk
    timestamp) must not raise; the row comes back flagged incomplete.
    `fold_events` is advertised as total for the replay verification."""
    events = [
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_CARD_CREATED, dedup_key="b1",
                        payload={"work_type": "feature", "tier": "not-an-int"}),
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_EXECUTOR_EXITED, dedup_key="att-1",
                        payload={"tokens": "lots", "wall_seconds": "soon"}),
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_PR_OPENED, dedup_key="opened:b1",
                        payload={"pr_opened_at": "garbage"}),
        ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                        kind=ev.KIND_PR_MERGED, dedup_key="b1",
                        payload={"merged_at": "also-garbage"}),
    ]
    row = fold_events(events)  # must not raise
    assert row is not None
    assert row.incomplete_metrics is True
    assert row.executor_tokens_total == 0  # junk token coerces to 0
    assert row.human_review_wall_seconds is None  # junk timestamps -> None


def test_fold_missing_work_type_is_incomplete() -> None:
    events = [ev.MetricsEvent(
        at="t", card_id="b1", tenant_id=TENANT,
        kind=ev.KIND_EXECUTOR_EXITED, dedup_key="att-1",
        payload={"tokens": 10, "wall_seconds": 1.0})]
    row = fold_events(events)
    assert row is not None
    assert row.work_type is None
    assert row.incomplete_metrics is True


# ---- LedgerWriter end to end -----------------------------------------


def test_writer_populates_card_metrics_row(writer) -> None:
    w, store, _paths = writer
    w.record_card_created(card_id="b1", tenant_id=TENANT,
                          work_type="feature", tier=3, pin_required=False)
    w.record_card_started(card_id="b1", tenant_id=TENANT,
                          attempt_trace_id="att-1",
                          started_at="2026-06-01T00:00:00Z")
    w.record_executor_exit(card_id="b1", tenant_id=TENANT,
                           attempt_trace_id="att-1",
                           started_at="2026-06-01T00:00:00Z",
                           finished_at="2026-06-01T00:10:00Z",
                           tokens=500, cost_usd=0.12)
    row = store.get_card_metrics(tenant_id=TENANT, card_id="b1")
    assert row is not None
    assert row.work_type == "feature"
    assert row.tier == 3
    assert row.agent_attempts == 1
    assert row.executor_tokens_total == 500
    assert row.agent_wall_seconds == 600.0
    assert row.executor_cost_usd == pytest.approx(0.12)


def test_writer_idempotent_replay_does_not_double_count(writer) -> None:
    w, store, _paths = writer
    w.record_card_created(card_id="b1", tenant_id=TENANT,
                          work_type="feature", tier=3, pin_required=False)
    for _ in range(3):  # replay the same attempt's exit three times
        w.record_executor_exit(card_id="b1", tenant_id=TENANT,
                               attempt_trace_id="att-1",
                               started_at="2026-06-01T00:00:00Z",
                               finished_at="2026-06-01T00:10:00Z",
                               tokens=500)
    row = store.get_card_metrics(tenant_id=TENANT, card_id="b1")
    assert row is not None
    assert row.agent_attempts == 1
    assert row.executor_tokens_total == 500


def test_writer_verifier_fail_then_merge(writer) -> None:
    w, store, _paths = writer
    w.record_card_created(card_id="b1", tenant_id=TENANT,
                          work_type="feature", tier=2, pin_required=False)
    w.record_verifier_decided(card_id="b1", tenant_id=TENANT,
                              attempt_trace_id="att-1", failed=True,
                              tokens=30)
    w.record_merge_gate(card_id="b1", tenant_id=TENANT, gate="auto")
    w.record_pr_merged(card_id="b1", tenant_id=TENANT,
                       merged_at="2026-06-01T02:00:00Z",
                       diff_lines_added=10, diff_lines_removed=2)
    row = store.get_card_metrics(tenant_id=TENANT, card_id="b1")
    assert row is not None
    assert row.rework_cycles == 1
    assert row.verifier_tokens_total == 30
    assert row.merge_gate == "auto"
    assert row.merged_at == "2026-06-01T02:00:00Z"
    assert row.diff_lines_added == 10


def test_writer_reviewer_spend_accumulates(writer) -> None:
    w, store, _paths = writer
    w.record_card_created(card_id="b1", tenant_id=TENANT,
                          work_type="feature", tier=4, pin_required=False)
    w.record_reviewer_spend(card_id="b1", tenant_id=TENANT,
                            call_id="sibling:att-1", tokens=200)
    w.record_reviewer_spend(card_id="b1", tenant_id=TENANT,
                            call_id="amendment:att-1", tokens=80)
    w.record_reviewer_spend(card_id="b1", tenant_id=TENANT,
                            call_id="sibling:att-1", tokens=200)  # replay
    row = store.get_card_metrics(tenant_id=TENANT, card_id="b1")
    assert row is not None
    assert row.reviewer_tokens_total == 280  # 200 + 80, replay ignored


def test_writer_regression_flags_parent(writer) -> None:
    w, store, _paths = writer
    w.record_card_created(card_id="parent", tenant_id=TENANT,
                          work_type="feature", tier=3, pin_required=False)
    w.record_regression(parent_card_id="parent", tenant_id=TENANT,
                        regressing_card_id="bugfix-9")
    row = store.get_card_metrics(tenant_id=TENANT, card_id="parent")
    assert row is not None
    assert row.regression_card_ids == ("bugfix-9",)


def test_writer_contract_outcome(writer) -> None:
    w, store, _paths = writer
    w.record_card_created(card_id="b1", tenant_id=TENANT,
                          work_type="contract", tier=1, pin_required=False)
    w.record_contract_outcome(card_id="b1", tenant_id=TENANT, survived=True)
    row = store.get_card_metrics(tenant_id=TENANT, card_id="b1")
    assert row is not None
    assert row.contract_survived is True


def test_fold_contract_survived_false_is_sticky() -> None:
    """An amendment (False) before OR after a clean done (True) wins --
    contract drift is permanent regardless of event order."""
    created = ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                              kind=ev.KIND_CARD_CREATED, dedup_key="b1",
                              payload={"work_type": "feature", "tier": 3})
    true_ev = ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                              kind=ev.KIND_CONTRACT_OUTCOME, dedup_key="b1",
                              payload={"contract_survived": True})
    false_ev = ev.MetricsEvent(at="t", card_id="b1", tenant_id=TENANT,
                               kind=ev.KIND_CONTRACT_OUTCOME, dedup_key="b1",
                               payload={"contract_survived": False})
    # True then False -> False (drift after a clean record).
    assert fold_events([created, true_ev, false_ev]).contract_survived is False
    # False then True -> False (sticky; a later clean done can't erase it).
    assert fold_events([created, false_ev, true_ev]).contract_survived is False
    # True only -> True.
    assert fold_events([created, true_ev]).contract_survived is True


# ---- spec 12.3 audit-log replay verification -------------------------


def test_replay_rebuilds_identical_row(writer) -> None:
    """Drive a full lifecycle through the writer, then rebuild the row
    purely from the JSONL log and assert it matches the live table.
    This is the section-12.3 replay verification."""
    w, store, paths = writer
    w.record_card_created(card_id="b1", tenant_id=TENANT,
                          work_type="feature", tier=3, pin_required=False,
                          contract_authored_at="2026-05-31T00:00:00Z")
    w.record_card_started(card_id="b1", tenant_id=TENANT,
                          attempt_trace_id="att-1",
                          started_at="2026-06-01T00:00:00Z")
    w.record_executor_exit(card_id="b1", tenant_id=TENANT,
                           attempt_trace_id="att-1",
                           started_at="2026-06-01T00:00:00Z",
                           finished_at="2026-06-01T00:10:00Z",
                           tokens=500, cost_usd=0.1)
    w.record_verifier_decided(card_id="b1", tenant_id=TENANT,
                              attempt_trace_id="att-1", failed=False,
                              tokens=40)
    w.record_merge_gate(card_id="b1", tenant_id=TENANT,
                        gate="sibling_review")
    w.record_pr_opened(card_id="b1", tenant_id=TENANT,
                       pr_opened_at="2026-06-01T00:30:00Z")
    w.record_pr_merged(card_id="b1", tenant_id=TENANT,
                       merged_at="2026-06-01T01:00:00Z",
                       diff_lines_added=120, diff_lines_removed=8)

    live = store.get_card_metrics(tenant_id=TENANT, card_id="b1")
    rebuilt = fold_events(
        ev.read_events_for_card(paths, card_id="b1", tenant_id=TENANT)
    )
    assert live is not None
    assert rebuilt is not None
    assert live == rebuilt
    # And the rebuilt row reflects the full lifecycle.
    assert live.merge_gate == "sibling_review"
    assert live.human_review_wall_seconds == 1800.0  # 00:30 -> 01:00
    assert live.contract_authored_at == "2026-05-31T00:00:00Z"
    assert live.verifier_tokens_total == 40
    assert live.rework_cycles == 0
