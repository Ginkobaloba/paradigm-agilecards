"""Confidence-gate ramp: per-bucket phase state machine (gate chunk 3).

`docs/design/confidence_driven_merge_gate.md` section 9. Each
`(work_type, tier)` bucket carries a ramp phase 1-4; advancement is
operator-explicit (`cards-runner stats ramp advance --confirm`), gated
on the section 9.3 evidence thresholds, and never autonomous. The
kill-switch direction (tightening) is the asymmetric automatic side and
lands with live-mode wiring in gate chunk 4; this module ships the
state store, the advancement gates, and the recommendation evaluation
that chunk 4's calibration loop will also call.

**Deviation from spec 9.5, documented:** the spec puts phase state in a
`phase_per_bucket` column on `metric_estimates`. That table is a
recomputable cache refreshed via INSERT OR REPLACE on every
recalibration -- REPLACE deletes and re-inserts the row, so a phase
column there would silently reset to its default each time
`stats recalibrate` runs. Phase state is operator policy, not derived
data, so it lives in its own small `gate_ramp` table (created by the
chunk-1-style DDL in `store/schema.py`) keyed the same way. Same
persistence guarantee the spec wanted, without the cache-refresh
footgun.

Phase semantics (spec 9.1-9.3):

1. shadow      -- gate records, chunk-4 tier routing decides (default)
2. narrow live -- gate routes; auto >= 0.95, sibling >= 0.85
3. widened     -- thresholds relax per config after live evidence
4. fitted      -- linear formula replaced by fitted model (gate-6)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..common.types import RuntimePaths, now_utc_iso
from . import events as ev
from .calibration import Calibration

PHASE_MIN: int = 1
PHASE_MAX: int = 4


class _Connection(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any: ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class RampState:
    """One bucket's ramp state. A bucket with no row is phase 1 with no
    alarm -- the conservative default the spec's migration rule sets."""

    tenant_id: str
    work_type: str
    tier: int
    phase: int = PHASE_MIN
    alarm_active: bool = False
    updated_at: str | None = None


@dataclass(frozen=True)
class AdvanceGates:
    """The section 9.3 advancement thresholds, in one place so chunk 4
    reuses them when the calibration loop emits recommendations."""

    phase1_min_shadow_n: int = 30
    phase2_min_live_n: int = 100
    phase2_max_top_band_regression: float = 0.05
    phase2_killswitch_quiet_days: int = 14
    phase3_min_live_n: int = 300


@dataclass(frozen=True)
class GateCheck:
    """One named advancement check with its evidence."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PhaseRecommendation:
    """The evaluation `stats ramp advance` prints and (when ready +
    confirmed) acts on."""

    tenant_id: str
    work_type: str
    tier: int
    current_phase: int
    next_phase: int
    ready: bool
    checks: tuple[GateCheck, ...]


class RampStore:
    """Read-write surface for the `gate_ramp` table.

    Same connection-sharing pattern as `MetricsStore`: construct via
    `from_repository` to ride the card store's connection."""

    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    @classmethod
    def from_repository(cls, repo: object) -> "RampStore":
        conn = getattr(repo, "_conn", None) or getattr(repo, "conn", None)
        if conn is None:
            raise TypeError(
                "ramp store requires a CardRepository with a SQL "
                "connection attribute (`_conn` or `conn`)"
            )
        return cls(conn)

    def get(
        self, *, tenant_id: str, work_type: str, tier: int
    ) -> RampState:
        cur = self._conn.execute(
            "SELECT phase, alarm_active, updated_at FROM gate_ramp"
            " WHERE tenant_id = ? AND work_type = ? AND tier = ?",
            (tenant_id, work_type, tier),
        )
        row = cur.fetchone()
        if row is None:
            return RampState(
                tenant_id=tenant_id, work_type=work_type, tier=tier
            )
        return RampState(
            tenant_id=tenant_id,
            work_type=work_type,
            tier=tier,
            phase=int(row[0] or PHASE_MIN),
            alarm_active=bool(row[1] or 0),
            updated_at=None if row[2] is None else str(row[2]),
        )

    def list_states(self, *, tenant_id: str) -> list[RampState]:
        cur = self._conn.execute(
            "SELECT work_type, tier, phase, alarm_active, updated_at"
            " FROM gate_ramp WHERE tenant_id = ?"
            " ORDER BY work_type, tier",
            (tenant_id,),
        )
        return [
            RampState(
                tenant_id=tenant_id,
                work_type=str(row[0]),
                tier=int(row[1]),
                phase=int(row[2] or PHASE_MIN),
                alarm_active=bool(row[3] or 0),
                updated_at=None if row[4] is None else str(row[4]),
            )
            for row in cur.fetchall()
        ]

    def set_phase(
        self, *, tenant_id: str, work_type: str, tier: int, phase: int
    ) -> RampState:
        if not PHASE_MIN <= phase <= PHASE_MAX:
            raise ValueError(
                f"phase must be in [{PHASE_MIN}, {PHASE_MAX}], got {phase}"
            )
        existing = self.get(
            tenant_id=tenant_id, work_type=work_type, tier=tier
        )
        at = now_utc_iso()
        self._conn.execute(
            "INSERT OR REPLACE INTO gate_ramp"
            " (tenant_id, work_type, tier, phase, alarm_active, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, work_type, tier, phase,
             int(existing.alarm_active), at),
        )
        return RampState(
            tenant_id=tenant_id, work_type=work_type, tier=tier,
            phase=phase, alarm_active=existing.alarm_active, updated_at=at,
        )

    def set_alarm(
        self, *, tenant_id: str, work_type: str, tier: int, active: bool
    ) -> RampState:
        """Flip the bucket's kill-switch alarm. The automatic trip is
        chunk-4 live-mode behavior; the manual clear (spec 9.4 step 4)
        is exposed now so the operator surface is complete."""
        existing = self.get(
            tenant_id=tenant_id, work_type=work_type, tier=tier
        )
        at = now_utc_iso()
        self._conn.execute(
            "INSERT OR REPLACE INTO gate_ramp"
            " (tenant_id, work_type, tier, phase, alarm_active, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, work_type, tier, existing.phase, int(active), at),
        )
        return RampState(
            tenant_id=tenant_id, work_type=work_type, tier=tier,
            phase=existing.phase, alarm_active=active, updated_at=at,
        )

    def commit(self) -> None:
        self._conn.commit()


def count_live_decisions(
    paths: RuntimePaths, *, tenant_id: str, work_type: str, tier: int
) -> int:
    """Count `gate_live_decision` events for one bucket. Zero until
    chunk 4 wires live mode; counting the kind now keeps the 2->3 and
    3->4 gates honest instead of special-cased."""
    n = 0
    for event in ev.read_events(paths):
        if event.kind != ev.KIND_GATE_LIVE_DECISION:
            continue
        if event.tenant_id != tenant_id:
            continue
        inputs = (event.payload.get("inputs") or {})
        if inputs.get("work_type") == work_type and inputs.get("tier") == tier:
            n += 1
    return n


def killswitch_quiet(
    paths: RuntimePaths, *, tenant_id: str
) -> bool:
    """True when no `gate_killswitch_tripped` event exists that is not
    followed by a clear. Chunk 4 emits these; until then the log has
    none and the check passes. The 14-day window refinement needs the
    live-mode event volume to matter and lands with chunk 4."""
    tripped = 0
    cleared = 0
    for event in ev.read_events(paths):
        if event.tenant_id != tenant_id:
            continue
        if event.kind == ev.KIND_GATE_KILLSWITCH_TRIPPED:
            tripped += 1
        elif event.kind == ev.KIND_GATE_KILLSWITCH_CLEARED:
            cleared += 1
    return tripped <= cleared


def evaluate_advance(
    state: RampState,
    calibration: Calibration,
    *,
    shadow_n: int,
    live_n: int = 0,
    killswitch_clear: bool = True,
    gates: AdvanceGates | None = None,
) -> PhaseRecommendation:
    """Evaluate the section 9.3 gates for `state.phase -> +1`. Pure.

    Returns the named checks with evidence so the CLI (and the chunk-4
    recommendation event) can show the operator exactly what passed and
    what is still short. A phase-4 bucket evaluates as not-ready with a
    single explanatory check."""
    g = gates or AdvanceGates()
    checks: list[GateCheck] = []

    if state.phase >= PHASE_MAX:
        checks.append(GateCheck(
            name="phase_ceiling", passed=False,
            detail=f"already at phase {PHASE_MAX}; no further advancement",
        ))
    elif state.phase == 1:
        checks.append(GateCheck(
            name="shadow_n", passed=shadow_n >= g.phase1_min_shadow_n,
            detail=f"shadow decisions {shadow_n} "
                   f"(need >= {g.phase1_min_shadow_n})",
        ))
        checks.append(GateCheck(
            name="calibration_monotonic", passed=calibration.monotonic,
            detail="monotonic" if calibration.monotonic
                   else "inverted band detected",
        ))
    elif state.phase == 2:
        top = _top_populated_band(calibration)
        top_rate_ok = (
            top is not None
            and top.regression_rate < g.phase2_max_top_band_regression
        )
        checks.append(GateCheck(
            name="live_n", passed=live_n >= g.phase2_min_live_n,
            detail=f"live decisions {live_n} "
                   f"(need >= {g.phase2_min_live_n})",
        ))
        checks.append(GateCheck(
            name="top_band_regression", passed=top_rate_ok,
            detail=(
                f"top band rate {top.regression_rate:.1%} "
                f"(need < {g.phase2_max_top_band_regression:.0%})"
                if top is not None else "no populated band"
            ),
        ))
        checks.append(GateCheck(
            name="calibration_monotonic", passed=calibration.monotonic,
            detail="monotonic" if calibration.monotonic
                   else "inverted band detected",
        ))
        checks.append(GateCheck(
            name="killswitch_quiet", passed=killswitch_clear,
            detail="no untripped kill-switch" if killswitch_clear
                   else "kill-switch tripped and not cleared",
        ))
    else:  # phase 3 -> 4
        checks.append(GateCheck(
            name="live_n", passed=live_n >= g.phase3_min_live_n,
            detail=f"live decisions {live_n} "
                   f"(need >= {g.phase3_min_live_n})",
        ))
        checks.append(GateCheck(
            name="calibration_monotonic", passed=calibration.monotonic,
            detail="monotonic" if calibration.monotonic
                   else "inverted band detected",
        ))
        # The fitted-model migration (gate-6) cannot be detected from
        # ledger data; it gates here unconditionally until it lands.
        checks.append(GateCheck(
            name="fitted_model_landed", passed=False,
            detail="gate-6 fitted-logistic migration not landed",
        ))

    if state.alarm_active:
        checks.append(GateCheck(
            name="alarm_inactive", passed=False,
            detail="bucket kill-switch alarm is active; clear it first",
        ))

    ready = bool(checks) and all(c.passed for c in checks)
    return PhaseRecommendation(
        tenant_id=state.tenant_id,
        work_type=state.work_type,
        tier=state.tier,
        current_phase=state.phase,
        next_phase=min(state.phase + 1, PHASE_MAX),
        ready=ready,
        checks=tuple(checks),
    )


def _top_populated_band(calibration: Calibration):
    for band in calibration.bands:  # already highest-first
        if band.n > 0:
            return band
    return None


__all__ = [
    "AdvanceGates",
    "GateCheck",
    "PHASE_MAX",
    "PHASE_MIN",
    "PhaseRecommendation",
    "RampState",
    "RampStore",
    "count_live_decisions",
    "evaluate_advance",
    "killswitch_quiet",
]
