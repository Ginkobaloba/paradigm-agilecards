"""Tests for the gate chunk 3 calibration read.

Pure banding/monotonicity math first, then the end-to-end read that
joins the shadow-decision event log against `card_metrics` regression
outcomes through a real SQLite store.
"""
from __future__ import annotations

from pathlib import Path

from cards_runner.common.types import RuntimePaths
from cards_runner.metrics import events as ev
from cards_runner.metrics.calibration import (
    buckets_in_shadow_log,
    calibrate,
    calibration_for_bucket,
    latest_per_card,
    read_shadow_decisions,
    render_table,
    shadow_decisions_from_events,
)
from cards_runner.metrics.store import MetricsStore
from cards_runner.store.sqlite_store import SqliteRepository


def _shadow_event(
    *,
    card_id: str,
    score: float,
    at: str = "2026-06-09T10:00:00Z",
    tenant_id: str = "default",
    work_type: str | None = "feature",
    tier: int | None = 3,
    outcome: str = "auto",
) -> ev.MetricsEvent:
    return ev.MetricsEvent(
        at=at, card_id=card_id, tenant_id=tenant_id,
        kind=ev.KIND_GATE_SHADOW_DECISION,
        dedup_key=f"shadow:{card_id}:{at}",
        payload={
            "outcome": outcome,
            "confidence_score": score,
            "raw_score": score,
            "escalators": [],
            "reason": "confidence_band",
            "inputs": {"work_type": work_type, "tier": tier},
        },
    )


def _decisions(*specs: tuple[str, float]) -> list:
    return shadow_decisions_from_events(
        [_shadow_event(card_id=cid, score=s) for cid, s in specs]
    )


# ---- parsing --------------------------------------------------------


def test_parse_skips_other_kinds_and_missing_scores() -> None:
    events = [
        _shadow_event(card_id="c1", score=0.9),
        ev.MetricsEvent(
            at="2026-06-09T10:00:00Z", card_id="c2", tenant_id="default",
            kind=ev.KIND_PR_MERGED, dedup_key="c2", payload={},
        ),
        ev.MetricsEvent(
            at="2026-06-09T10:00:00Z", card_id="c3", tenant_id="default",
            kind=ev.KIND_GATE_SHADOW_DECISION, dedup_key="x",
            payload={"outcome": "auto"},  # no confidence_score
        ),
    ]
    decisions = shadow_decisions_from_events(events)
    assert [d.card_id for d in decisions] == ["c1"]
    assert decisions[0].work_type == "feature"
    assert decisions[0].tier == 3


def test_latest_per_card_keeps_last_in_file_order() -> None:
    events = [
        _shadow_event(card_id="c1", score=0.4, at="2026-06-09T10:00:00Z"),
        _shadow_event(card_id="c1", score=0.9, at="2026-06-09T11:00:00Z"),
        _shadow_event(card_id="c2", score=0.7, at="2026-06-09T10:30:00Z"),
    ]
    latest = latest_per_card(shadow_decisions_from_events(events))
    by_id = {d.card_id: d for d in latest}
    assert len(latest) == 2
    assert by_id["c1"].confidence_score == 0.9


# ---- banding math ---------------------------------------------------


def test_bands_are_deciles_highest_first_and_top_includes_one() -> None:
    cal = calibrate(
        _decisions(("c1", 1.0), ("c2", 0.95), ("c3", 0.05)),
        frozenset(),
    )
    assert len(cal.bands) == 10
    top, bottom = cal.bands[0], cal.bands[-1]
    assert (top.lo, top.hi) == (0.9, 1.0)
    assert top.n == 2  # 1.0 lands in the top band, not out of range
    assert (bottom.lo, bottom.hi) == (0.0, 0.1)
    assert bottom.n == 1


def test_regression_rates_join_on_card_id() -> None:
    cal = calibrate(
        _decisions(("good", 0.95), ("bad", 0.95), ("low", 0.30)),
        frozenset({"bad"}),
    )
    top = cal.bands[0]
    assert top.n == 2
    assert top.regressions == 1
    assert top.regression_rate == 0.5
    assert cal.overall_n == 3
    assert cal.overall_regressions == 1


def test_monotonic_true_when_lower_bands_regress_more() -> None:
    cal = calibrate(
        _decisions(
            ("a", 0.95), ("b", 0.95),          # top band: 0%
            ("c", 0.55), ("d", 0.55),          # mid band: 50%
        ),
        frozenset({"d"}),
    )
    assert cal.monotonic is True


def test_monotonic_false_on_inversion() -> None:
    # The HIGH band regresses; the low band does not: inverted.
    cal = calibrate(
        _decisions(("a", 0.95), ("b", 0.55)),
        frozenset({"a"}),
    )
    assert cal.monotonic is False


def test_monotonic_allows_all_zero_ties() -> None:
    """Adjacent equal (zero) rates must not fail monotonicity -- a
    young system with no regressions anywhere is calibrated, not
    inverted. Strictness here would block phase advancement on noise."""
    cal = calibrate(
        _decisions(("a", 0.95), ("b", 0.55), ("c", 0.15)),
        frozenset(),
    )
    assert cal.monotonic is True


def test_window_keeps_most_recent_cards_by_timestamp() -> None:
    events = [
        _shadow_event(card_id="old", score=0.2, at="2026-06-01T00:00:00Z"),
        _shadow_event(card_id="new1", score=0.9, at="2026-06-08T00:00:00Z"),
        _shadow_event(card_id="new2", score=0.9, at="2026-06-09T00:00:00Z"),
    ]
    cal = calibrate(
        shadow_decisions_from_events(events), frozenset(), window_cards=2
    )
    assert cal.overall_n == 2
    assert cal.bands[0].n == 2  # both window survivors in the top band
    assert all(b.n == 0 for b in cal.bands[1:])


def test_rework_reevaluation_uses_last_decision() -> None:
    events = [
        _shadow_event(card_id="c1", score=0.2, at="2026-06-08T00:00:00Z"),
        _shadow_event(card_id="c1", score=0.95, at="2026-06-09T00:00:00Z"),
    ]
    cal = calibrate(shadow_decisions_from_events(events), frozenset())
    assert cal.overall_n == 1
    assert cal.bands[0].n == 1


# ---- end-to-end read ------------------------------------------------


def _seed_metrics_row(
    conn, *, card_id: str, regression_ids: str,
    work_type: str = "feature", tier: int = 3,
) -> None:
    conn.execute(
        "INSERT INTO card_metrics (tenant_id, card_id, work_type, tier,"
        " regression_card_ids, incomplete_metrics, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 0, ?)",
        ("default", card_id, work_type, tier, regression_ids,
         "2026-06-09T10:00:00Z"),
    )
    conn.commit()


def test_calibration_for_bucket_joins_log_and_ledger(
    paths: RuntimePaths, store_path: Path,
) -> None:
    ev.append_event(paths, _shadow_event(card_id="clean", score=0.96))
    ev.append_event(paths, _shadow_event(card_id="regr", score=0.55))
    ev.append_event(paths, _shadow_event(
        card_id="other-bucket", score=0.96, work_type="bugfix", tier=1,
    ))
    repo = SqliteRepository.open(str(store_path))
    try:
        _seed_metrics_row(repo._conn, card_id="clean", regression_ids="[]")
        _seed_metrics_row(
            repo._conn, card_id="regr", regression_ids='["bFIX-01"]'
        )
        store = MetricsStore.from_repository(repo)
        cal = calibration_for_bucket(
            store, paths,
            tenant_id="default", work_type="feature", tier=3,
        )
    finally:
        repo.close()
    assert cal.overall_n == 2  # other-bucket excluded
    assert cal.overall_regressions == 1
    assert cal.monotonic is True  # 0% top band, 100% mid band
    table = render_table(cal)
    assert "feature/tier3" in table
    assert "monotonic" in table


def test_buckets_in_shadow_log_excludes_unbucketed(
    paths: RuntimePaths,
) -> None:
    ev.append_event(paths, _shadow_event(card_id="c1", score=0.9))
    ev.append_event(paths, _shadow_event(
        card_id="c2", score=0.9, work_type=None, tier=None,
    ))
    ev.append_event(paths, _shadow_event(
        card_id="c3", score=0.9, work_type="bugfix", tier=1,
    ))
    assert buckets_in_shadow_log(paths, tenant_id="default") == [
        ("bugfix", 1), ("feature", 3),
    ]


def test_read_shadow_decisions_filters_tenant(
    paths: RuntimePaths,
) -> None:
    ev.append_event(paths, _shadow_event(card_id="mine", score=0.9))
    ev.append_event(paths, _shadow_event(
        card_id="theirs", score=0.9, tenant_id="other",
    ))
    decisions = read_shadow_decisions(paths, tenant_id="default")
    assert [d.card_id for d in decisions] == ["mine"]
