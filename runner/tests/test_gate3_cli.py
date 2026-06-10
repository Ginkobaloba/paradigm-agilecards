"""End-to-end tests for the gate chunk 3 CLI surfaces.

`cards-runner stats calibration` and `cards-runner stats ramp
show / advance`, run through `main()` against a real store + event log.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cards_runner.cli.__main__ import main
from cards_runner.common.types import RuntimePaths
from cards_runner.metrics import events as ev
from cards_runner.metrics.ramp import RampStore
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


def _common(todo_root: Path, store_path: Path) -> list[str]:
    return ["--todo-root", str(todo_root), "--store", f"sqlite:{store_path}"]


def _seed_shadow(paths: RuntimePaths, n: int, *, score: float = 0.96) -> None:
    for i in range(n):
        ev.append_event(paths, _shadow_event(
            card_id=f"card-{i:03d}", score=score,
            at=f"2026-06-09T10:{i:02d}:00Z",
        ))


# ---- stats calibration ----------------------------------------------


def test_calibration_no_data_message(
    todo_root: Path, store_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    rc = main(["stats", "calibration", *_common(todo_root, store_path)])
    assert rc == 0
    assert "no gate shadow decisions" in capsys.readouterr().out


def test_calibration_requires_both_bucket_args(
    todo_root: Path, store_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    rc = main([
        "stats", "calibration", "--work-type", "feature",
        *_common(todo_root, store_path),
    ])
    assert rc == 2
    assert "must be given together" in capsys.readouterr().err


def test_calibration_table_and_json(
    todo_root: Path, paths: RuntimePaths, store_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _seed_shadow(paths, 3)
    rc = main([
        "stats", "calibration", "--work-type", "feature", "--tier", "3",
        *_common(todo_root, store_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "feature/tier3" in out
    assert "monotonic" in out

    rc = main([
        "stats", "calibration", "--json", *_common(todo_root, store_path),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["buckets"][0]["work_type"] == "feature"
    assert payload["buckets"][0]["overall_n"] == 3
    assert payload["buckets"][0]["monotonic"] is True


# ---- stats ramp -----------------------------------------------------


def test_ramp_show_defaults_shadow_bucket_to_phase_1(
    todo_root: Path, paths: RuntimePaths, store_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _seed_shadow(paths, 2)
    rc = main([
        "stats", "ramp", "show", "--json", *_common(todo_root, store_path),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    bucket = payload["buckets"][0]
    assert (bucket["work_type"], bucket["tier"]) == ("feature", 3)
    assert bucket["phase"] == 1
    assert bucket["shadow_n"] == 2
    assert bucket["advance_ready"] is False


def test_ramp_advance_refuses_below_gates(
    todo_root: Path, paths: RuntimePaths, store_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _seed_shadow(paths, 5)
    rc = main([
        "stats", "ramp", "advance", "--bucket", "feature:3", "--confirm",
        *_common(todo_root, store_path),
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "gates NOT met" in out
    # The refused attempt still leaves an audit trail.
    kinds = [e.kind for e in ev.read_events(paths)]
    assert ev.KIND_GATE_PHASE_RECOMMENDATION in kinds
    assert ev.KIND_GATE_PHASE_ADVANCED not in kinds


def test_ramp_advance_dry_run_changes_nothing(
    todo_root: Path, paths: RuntimePaths, store_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _seed_shadow(paths, 30)
    rc = main([
        "stats", "ramp", "advance", "--bucket", "feature:3",
        *_common(todo_root, store_path),
    ])
    assert rc == 0
    assert "dry run" in capsys.readouterr().out
    repo = SqliteRepository.open(str(store_path))
    try:
        state = RampStore.from_repository(repo).get(
            tenant_id="default", work_type="feature", tier=3
        )
        assert state.phase == 1
    finally:
        repo.close()


def test_ramp_advance_confirm_applies_and_audits(
    todo_root: Path, paths: RuntimePaths, store_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _seed_shadow(paths, 30)
    rc = main([
        "stats", "ramp", "advance", "--bucket", "feature:3", "--confirm",
        *_common(todo_root, store_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "gates green" in out
    assert "advanced: now phase 2" in out
    repo = SqliteRepository.open(str(store_path))
    try:
        state = RampStore.from_repository(repo).get(
            tenant_id="default", work_type="feature", tier=3
        )
        assert state.phase == 2
    finally:
        repo.close()
    kinds = [e.kind for e in ev.read_events(paths)]
    assert ev.KIND_GATE_PHASE_RECOMMENDATION in kinds
    assert ev.KIND_GATE_PHASE_ADVANCED in kinds


def test_ramp_advance_rejects_malformed_bucket(
    todo_root: Path, store_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    rc = main([
        "stats", "ramp", "advance", "--bucket", "feature3",
        *_common(todo_root, store_path),
    ])
    assert rc == 2
    assert "work_type:tier" in capsys.readouterr().err
