"""Tests for the confidence-gate decision engine (gate chunk 2 core).

Covers the pure scoring engine (`ConfidenceGate`), the diff-stats parser
and glob matchers (`DiffStats`), and the ledger-backed bucket-history
reader. No daemon wiring / shadow recording yet (that is gate-2b).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cards_runner.daemon.confidence_gate import (
    OUTCOME_AUTO,
    OUTCOME_HUMAN,
    OUTCOME_SIBLING,
    BucketHistory,
    ConfidenceGate,
    ConfidenceGateConfig,
    GateInputs,
    read_bucket_history,
)
from cards_runner.daemon.diff_stats import DiffStats, matches_any_glob
from cards_runner.metrics.store import MetricsStore
from cards_runner.store.sqlite_store import SqliteRepository
from cards_runner.verifier.risk_factor import RiskFactor


# ---- DiffStats -------------------------------------------------------


def test_numstat_parse_counts_and_files() -> None:
    text = "10\t2\tsrc/a.py\n5\t0\tsrc/b.py\n-\t-\tassets/logo.png\n"
    d = DiffStats.from_numstat(text)
    assert d.lines_added == 15
    assert d.lines_removed == 2
    assert d.total_lines == 17
    assert d.files == ("src/a.py", "src/b.py", "assets/logo.png")


def test_numstat_empty() -> None:
    d = DiffStats.from_numstat("")
    assert d.total_lines == 0 and d.files == ()


def test_glob_matching() -> None:
    assert matches_any_glob("src/auth/login.py", ("src/auth/**",))
    assert matches_any_glob("src/migrations/0001.py", ("src/migrations/**",))
    assert matches_any_glob(".env.local", ("**/.env*",))
    assert matches_any_glob("config/.env", ("**/.env*",))
    assert matches_any_glob("db/schema.sql", ("**/schema*.sql",))
    assert not matches_any_glob("src/app/main.py", ("src/auth/**",))
    # `**/` matches zero leading segments too.
    assert matches_any_glob("a/b", ("a/**/b",))
    assert matches_any_glob("a/x/y/b", ("a/**/b",))


def test_diffstats_any_path_matches() -> None:
    d = DiffStats(files=("src/auth/x.py", "README.md"))
    assert d.any_path_matches(("src/auth/**",))
    assert not d.any_path_matches(("src/crypto/**",))


# ---- the soft formula: spec section 14 worked example ----------------


def test_worked_example_tier3_feature_routes_human() -> None:
    """Spec section 14: tier-3 feature, all-deterministic-first-try,
    haiku-cleared subjective (conf 0.91), 220-line diff, one medium risk
    factor, bucket regression 0.024 -> raw 0.56, score ~0.533 -> human."""
    inputs = GateInputs(
        work_type="feature", tier=3,
        all_deterministic_first_try=True,
        subjective_cleared_tier="haiku",
        cascade_climbs=0, rework_cycles=0,
        verifier_confidence=0.91,
        diff_total_lines=220,
        risk_factors=(RiskFactor(kind="incomplete_test_coverage",
                                 severity="medium", description="no test"),),
    )
    gate = ConfidenceGate(ConfidenceGateConfig())
    dec = gate.decide(inputs, BucketHistory(regression_rate=0.024, n_samples=42))
    assert dec.raw_score == pytest.approx(0.56, abs=1e-9)
    assert dec.confidence_score == pytest.approx(0.53312, abs=1e-4)
    assert dec.outcome == OUTCOME_HUMAN
    assert dec.reason == "confidence_band"
    assert dec.escalators == ()


def test_clean_card_routes_auto() -> None:
    inputs = GateInputs(
        work_type="refactor", tier=2,
        all_deterministic_first_try=True,
        subjective_cleared_tier="haiku",
        sibling_decision="approve",
        diff_is_test_only=True,
        diff_within_declared_scope=True,
        diff_total_lines=20,
    )
    dec = ConfidenceGate().decide(inputs, BucketHistory())
    assert dec.confidence_score == pytest.approx(1.0)
    assert dec.outcome == OUTCOME_AUTO


def test_mid_score_routes_sibling() -> None:
    # 0.50 + 0.10 (det) + 0.05 (haiku) + 0.20 (sibling approve) = 0.85.
    # verifier_confidence pinned to 0.85 so it adds no bonus (the > check
    # is strict), keeping the arithmetic exact.
    inputs = GateInputs(
        work_type="feature", tier=3,
        all_deterministic_first_try=True,
        subjective_cleared_tier="haiku",
        sibling_decision="approve",
        verifier_confidence=0.85,
        diff_total_lines=10,
    )
    dec = ConfidenceGate().decide(inputs, BucketHistory())
    assert dec.confidence_score == pytest.approx(0.85)
    assert dec.outcome == OUTCOME_SIBLING


# ---- hard escalators -------------------------------------------------


def _strong_inputs(**over: object) -> GateInputs:
    base = dict(
        work_type="feature", tier=2,
        all_deterministic_first_try=True,
        subjective_cleared_tier="haiku",
        sibling_decision="approve",
        diff_is_test_only=True, diff_within_declared_scope=True,
        diff_total_lines=10,
    )
    base.update(over)
    return GateInputs(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value,escalator", [
    ("pin_required", True, "pin_required"),
    ("sensitive_path_touched", True, "sensitive_path_touched"),
    ("schema_migration_in_diff", True, "schema_migration_in_diff"),
    ("new_external_dependency", True, "new_external_dependency"),
    ("subjective_cleared_tier", "opus", "subjective_cascade_opus_used"),
    ("sibling_decision", "request_changes", "sibling_disagreement"),
    ("change_request_unresolved", True, "executor_change_request_unresolved"),
    ("verifier_incomplete_metrics", True, "verifier_incomplete_metrics"),
])
def test_hard_escalator_forces_human(field: str, value: object,
                                     escalator: str) -> None:
    """Each hard escalator forces human_review even on an otherwise
    auto-clean card."""
    inputs = _strong_inputs(**{field: value})
    dec = ConfidenceGate().decide(inputs, BucketHistory())
    assert dec.outcome == OUTCOME_HUMAN
    assert dec.reason == "hard_escalator"
    assert escalator in dec.escalators
    # The score is still recorded for calibration.
    assert dec.raw_score is not None


def test_high_severity_risk_factor_escalates() -> None:
    inputs = _strong_inputs(risk_factors=(
        RiskFactor(kind="raw_sql", severity="high", description="x"),
    ))
    dec = ConfidenceGate().decide(inputs, BucketHistory())
    assert dec.outcome == OUTCOME_HUMAN
    assert "risk_factor_high_severity" in dec.escalators


def test_large_diff_escalates() -> None:
    inputs = _strong_inputs(diff_total_lines=600)  # > default 300
    dec = ConfidenceGate().decide(inputs, BucketHistory())
    assert "large_diff" in dec.escalators


def test_regression_alarm_escalates() -> None:
    dec = ConfidenceGate().decide(_strong_inputs(),
                                  BucketHistory(alarm_active=True))
    assert "regression_rate_alarm_active" in dec.escalators


def test_policy_escalator_cannot_be_disabled() -> None:
    """`hard_escalators_disabled` can drop soft escalators but never the
    policy set (spec 3.3)."""
    cfg = ConfidenceGateConfig(hard_escalators_disabled=(
        "pin_required", "sensitive_path_touched", "large_diff",
    ))
    gate = ConfidenceGate(cfg)
    # pin_required is policy -> still fires.
    assert "pin_required" in gate.decide(
        _strong_inputs(pin_required=True), BucketHistory()).escalators
    # large_diff is a soft escalator -> can be disabled.
    big = gate.decide(_strong_inputs(diff_total_lines=600), BucketHistory())
    assert "large_diff" not in big.escalators


def test_historical_floor_compresses_score() -> None:
    """A bucket with a bad recent record compresses the score."""
    inputs = _strong_inputs(sibling_decision=None, diff_is_test_only=False,
                            diff_within_declared_scope=False)
    clean = ConfidenceGate().decide(inputs, BucketHistory(regression_rate=0.0))
    floored = ConfidenceGate().decide(
        inputs, BucketHistory(regression_rate=0.15))
    assert floored.confidence_score < clean.confidence_score
    assert floored.raw_score == clean.raw_score  # raw unchanged; floor differs


def test_is_live_shadow_default() -> None:
    assert ConfidenceGate().is_live() is False
    assert ConfidenceGate(ConfidenceGateConfig(mode="live")).is_live() is True


# ---- bucket-history reader -------------------------------------------


def _seed(conn: object, card_id: str, regression: str) -> None:
    conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO card_metrics (tenant_id, card_id, work_type, tier,"
        " regression_card_ids, incomplete_metrics) VALUES"
        " ('default', ?, 'feature', 3, ?, 0)",
        (card_id, regression),
    )
    conn.commit()  # type: ignore[attr-defined]


def test_read_bucket_history_from_store(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        store = MetricsStore.from_repository(repo)
        conn = repo._conn  # type: ignore[attr-defined]
        _seed(conn, "c1", "[]")
        _seed(conn, "c2", '["bugfix-9"]')   # regressed
        _seed(conn, "c3", "[]")
        _seed(conn, "c4", '["bugfix-10"]')  # regressed
        h = read_bucket_history(store, tenant_id="default",
                                work_type="feature", tier=3)
        assert h.n_samples == 4
        assert h.regression_rate == pytest.approx(0.5)
        # 0.5 > 2 * 0.05 target -> alarm.
        assert h.alarm_active is True
    finally:
        repo.close()


def test_read_bucket_history_empty_is_neutral(store_path: Path) -> None:
    repo = SqliteRepository.open(str(store_path))
    try:
        store = MetricsStore.from_repository(repo)
        h = read_bucket_history(store, tenant_id="default",
                                work_type="feature", tier=3)
        assert h.n_samples == 0
        assert h.regression_rate == 0.0
        assert h.alarm_active is False
    finally:
        repo.close()
