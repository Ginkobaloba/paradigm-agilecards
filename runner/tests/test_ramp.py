"""Tests for the gate chunk 3 ramp state machine.

The load-bearing properties: phase state persists in `gate_ramp` and
survives recalibration of `metric_estimates`; advancement gates match
spec section 9.3; advancement is operator-explicit (+1 only, never
autonomous); the alarm blocks advancement.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cards_runner.common.types import RuntimePaths
from cards_runner.metrics import events as ev
from cards_runner.metrics import load_priors, recalibrate_bucket
from cards_runner.metrics.calibration import calibrate
from cards_runner.metrics.ramp import (
    PHASE_MAX,
    AdvanceGates,
    RampState,
    RampStore,
    count_live_decisions,
    evaluate_advance,
    killswitch_quiet,
)
from cards_runner.metrics.store import MetricsStore
from cards_runner.store.sqlite_store import SqliteRepository


def _shadow_event(
    *, card_id: str, score: float, at: str = "2026-06-09T10:00:00Z",
) -> ev.MetricsEvent:
    return ev.MetricsEvent(
        at=at, card_id=card_id, tenant_id="default",
        kind=ev.KIND_GATE_SHADOW_DECISION,
        dedup_key=f"shadow:{card_id}:{at}",
        payload={
            "outcome": "auto", "confidence_score": score,
            "raw_score": score, "escalators": [],
            "reason": "confidence_band",
            "inputs": {"work_type": "feature", "tier": 3},
        },
    )


def _calibration(*, monotonic_inputs: bool = True):
    """A small calibration: monotonic (clean) or inverted on demand."""
    specs = [("a", 0.95), ("b", 0.55)]
    regressed = frozenset() if monotonic_inputs else frozenset({"a"})
    return calibrate(
        [d for d in _decisions(specs)], regressed,
        work_type="feature", tier=3,
    )


def _decisions(specs):
    from cards_runner.metrics.calibration import (
        shadow_decisions_from_events,
    )
    return shadow_decisions_from_events(
        [_shadow_event(card_id=cid, score=s) for cid, s in specs]
    )


# ---- store ----------------------------------------------------------


def test_missing_row_defaults_to_phase_1(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        ramp = RampStore.from_repository(repo)
        state = ramp.get(tenant_id="default", work_type="feature", tier=3)
        assert state.phase == 1
        assert state.alarm_active is False
    finally:
        repo.close()


def test_set_phase_roundtrip_and_alarm_preserved(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        ramp = RampStore.from_repository(repo)
        ramp.set_alarm(
            tenant_id="default", work_type="feature", tier=3, active=True
        )
        ramp.set_phase(
            tenant_id="default", work_type="feature", tier=3, phase=2
        )
        ramp.commit()
        state = ramp.get(tenant_id="default", work_type="feature", tier=3)
        assert state.phase == 2
        assert state.alarm_active is True  # set_phase must not clear it
        states = ramp.list_states(tenant_id="default")
        assert [(s.work_type, s.tier, s.phase) for s in states] == [
            ("feature", 3, 2)
        ]
    finally:
        repo.close()


def test_set_phase_rejects_out_of_range(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        ramp = RampStore.from_repository(repo)
        with pytest.raises(ValueError):
            ramp.set_phase(
                tenant_id="default", work_type="feature", tier=3, phase=5
            )
        with pytest.raises(ValueError):
            ramp.set_phase(
                tenant_id="default", work_type="feature", tier=3, phase=0
            )
    finally:
        repo.close()


def test_phase_survives_metric_estimates_recalibration(
    store_path: Path,
) -> None:
    """The reason `gate_ramp` is its own table: `stats recalibrate`
    refreshes `metric_estimates` via INSERT OR REPLACE, and ramp phase
    must not reset when that happens."""
    repo = SqliteRepository.open(str(store_path))
    try:
        ramp = RampStore.from_repository(repo)
        ramp.set_phase(
            tenant_id="default", work_type="feature", tier=3, phase=2
        )
        ramp.commit()
        store = MetricsStore.from_repository(repo)
        recalibrate_bucket(
            store, load_priors(),
            tenant_id="default", work_type="feature", tier=3,
            at="2026-06-09T10:00:00Z",
        )
        repo._conn.commit()
        state = ramp.get(tenant_id="default", work_type="feature", tier=3)
        assert state.phase == 2
    finally:
        repo.close()


# ---- advancement gates ----------------------------------------------


def _state(phase: int, *, alarm: bool = False) -> RampState:
    return RampState(
        tenant_id="default", work_type="feature", tier=3,
        phase=phase, alarm_active=alarm,
    )


def test_phase1_not_ready_below_shadow_n_floor() -> None:
    rec = evaluate_advance(_state(1), _calibration(), shadow_n=29)
    assert rec.ready is False
    assert rec.next_phase == 2
    failed = [c.name for c in rec.checks if not c.passed]
    assert failed == ["shadow_n"]


def test_phase1_ready_at_floor_with_monotonic_calibration() -> None:
    rec = evaluate_advance(_state(1), _calibration(), shadow_n=30)
    assert rec.ready is True


def test_phase1_blocked_by_inverted_calibration() -> None:
    rec = evaluate_advance(
        _state(1), _calibration(monotonic_inputs=False), shadow_n=30
    )
    assert rec.ready is False
    failed = [c.name for c in rec.checks if not c.passed]
    assert failed == ["calibration_monotonic"]


def test_phase2_requires_live_evidence() -> None:
    rec = evaluate_advance(_state(2), _calibration(), shadow_n=500, live_n=0)
    assert rec.ready is False
    failed = {c.name for c in rec.checks if not c.passed}
    assert "live_n" in failed


def test_phase2_top_band_regression_gate() -> None:
    # Top band regresses at 100%: way over the 5% ceiling.
    cal = calibrate(
        _decisions([("a", 0.97)]), frozenset({"a"}),
        work_type="feature", tier=3,
    )
    rec = evaluate_advance(_state(2), cal, shadow_n=500, live_n=100)
    failed = {c.name for c in rec.checks if not c.passed}
    assert "top_band_regression" in failed


def test_phase3_gated_on_fitted_model() -> None:
    rec = evaluate_advance(
        _state(3), _calibration(), shadow_n=500, live_n=300
    )
    assert rec.ready is False
    failed = {c.name for c in rec.checks if not c.passed}
    assert failed == {"fitted_model_landed"}


def test_phase4_is_the_ceiling() -> None:
    rec = evaluate_advance(_state(4), _calibration(), shadow_n=10_000)
    assert rec.ready is False
    assert rec.next_phase == PHASE_MAX
    assert rec.checks[0].name == "phase_ceiling"


def test_alarm_blocks_advancement_at_any_phase() -> None:
    rec = evaluate_advance(
        _state(1, alarm=True), _calibration(), shadow_n=100
    )
    assert rec.ready is False
    failed = {c.name for c in rec.checks if not c.passed}
    assert "alarm_inactive" in failed


def test_gates_are_configurable() -> None:
    rec = evaluate_advance(
        _state(1), _calibration(), shadow_n=5,
        gates=AdvanceGates(phase1_min_shadow_n=5),
    )
    assert rec.ready is True


# ---- event-log reads ------------------------------------------------


def test_count_live_decisions_filters_bucket(paths: RuntimePaths) -> None:
    for card_id, wt, tier in (
        ("c1", "feature", 3), ("c2", "feature", 3), ("c3", "bugfix", 1),
    ):
        ev.append_event(paths, ev.MetricsEvent(
            at="2026-06-09T10:00:00Z", card_id=card_id,
            tenant_id="default", kind=ev.KIND_GATE_LIVE_DECISION,
            dedup_key=f"live:{card_id}",
            payload={"inputs": {"work_type": wt, "tier": tier}},
        ))
    assert count_live_decisions(
        paths, tenant_id="default", work_type="feature", tier=3
    ) == 2


def test_killswitch_quiet_until_untripped_event(
    paths: RuntimePaths,
) -> None:
    assert killswitch_quiet(paths, tenant_id="default") is True
    ev.append_event(paths, ev.MetricsEvent(
        at="2026-06-09T10:00:00Z", card_id="bucket:feature:3",
        tenant_id="default", kind=ev.KIND_GATE_KILLSWITCH_TRIPPED,
        dedup_key="ks:1", payload={},
    ))
    assert killswitch_quiet(paths, tenant_id="default") is False
    ev.append_event(paths, ev.MetricsEvent(
        at="2026-06-09T11:00:00Z", card_id="bucket:feature:3",
        tenant_id="default", kind=ev.KIND_GATE_KILLSWITCH_CLEARED,
        dedup_key="ks:2", payload={},
    ))
    assert killswitch_quiet(paths, tenant_id="default") is True
