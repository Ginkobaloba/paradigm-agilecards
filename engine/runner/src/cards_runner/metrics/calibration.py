"""Confidence-gate calibration read (gate chunk 3).

`docs/design/confidence_driven_merge_gate.md` sections 7.3 and 8. The
gate-2b wiring records a `gate_shadow_decision` event per verifier-pass
card; this module joins those decisions against each card's regression
outcome (a non-empty `card_metrics.regression_card_ids`, same source of
truth as the historical floor in `read_bucket_history`) and produces a
per-bucket, per-confidence-band calibration table plus a monotonicity
verdict. That table is what gates ramp phase advancement (`ramp.py`)
and what `cards-runner stats calibration` prints.

Design notes:

- **Latest decision per card.** A card can accrue several shadow
  decisions across rework attempts (the writer deliberately retains all
  of them). Calibration uses the LAST decision in file order -- the one
  closest to the merge that did or did not regress. Earlier decisions
  describe attempts that never shipped.
- **Monotonicity is non-strict.** The spec's calibration sketch asks
  whether regression rate strictly decreases as the band rises; a
  young system with several all-zero bands would fail a strict test on
  ties alone, which would block phase advancement on noise rather than
  on miscalibration. Adjacent equal rates are therefore allowed; only
  an inversion (a HIGHER-confidence band regressing MORE than a
  lower one) marks the system miscalibrated. Empty bands are skipped,
  not treated as zero.
- **Pure math, thin I/O.** `calibrate` is a pure function over parsed
  decisions + a regressed-id set so the banding and monotonicity logic
  is exhaustively unit-testable; `calibration_for_bucket` is the thin
  read that wires it to the event log and the ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..common.types import RuntimePaths
from . import events as ev
from .store import MetricsStore

DEFAULT_WINDOW_CARDS: int = 100
DEFAULT_N_BANDS: int = 10


@dataclass(frozen=True)
class ShadowDecision:
    """One parsed `gate_shadow_decision` event."""

    card_id: str
    tenant_id: str
    at: str
    outcome: str
    confidence_score: float
    raw_score: float | None
    escalators: tuple[str, ...]
    reason: str
    work_type: str | None
    tier: int | None


@dataclass(frozen=True)
class CalibrationBand:
    """One confidence band's track record. `lo` inclusive; `hi`
    exclusive except for the top band, which includes 1.0."""

    lo: float
    hi: float
    n: int
    regressions: int
    regression_rate: float


@dataclass(frozen=True)
class Calibration:
    """The spec 7.3 read result for one `(work_type, tier)` bucket."""

    work_type: str | None
    tier: int | None
    bands: tuple[CalibrationBand, ...]  # highest confidence first
    monotonic: bool
    overall_n: int
    overall_regressions: int
    overall_regression_rate: float


def shadow_decisions_from_events(
    events: Iterable[ev.MetricsEvent],
) -> list[ShadowDecision]:
    """Parse `gate_shadow_decision` events, in file order.

    An event without a usable `confidence_score` is skipped: it cannot
    be banded, and the log's malformed-line tolerance (one bad entry
    must not poison the read) applies at this layer too."""
    decisions: list[ShadowDecision] = []
    for event in events:
        if event.kind != ev.KIND_GATE_SHADOW_DECISION:
            continue
        payload = event.payload
        try:
            score = float(payload["confidence_score"])
        except (KeyError, TypeError, ValueError):
            continue
        inputs = payload.get("inputs") or {}
        raw = payload.get("raw_score")
        tier = inputs.get("tier")
        decisions.append(ShadowDecision(
            card_id=event.card_id,
            tenant_id=event.tenant_id,
            at=event.at,
            outcome=str(payload.get("outcome", "")),
            confidence_score=score,
            raw_score=None if raw is None else float(raw),
            escalators=tuple(
                str(e) for e in (payload.get("escalators") or ())
            ),
            reason=str(payload.get("reason", "")),
            work_type=(
                None if inputs.get("work_type") is None
                else str(inputs["work_type"])
            ),
            tier=None if tier is None else int(tier),
        ))
    return decisions


def read_shadow_decisions(
    paths: RuntimePaths, *, tenant_id: str
) -> list[ShadowDecision]:
    """Read one tenant's shadow decisions from the metrics event log."""
    return [
        d for d in shadow_decisions_from_events(ev.read_events(paths))
        if d.tenant_id == tenant_id
    ]


def latest_per_card(
    decisions: Iterable[ShadowDecision],
) -> list[ShadowDecision]:
    """Collapse to each card's last decision, preserving file order."""
    by_card: dict[str, ShadowDecision] = {}
    for d in decisions:
        by_card[d.card_id] = d
    return list(by_card.values())


def buckets_in_shadow_log(
    paths: RuntimePaths, *, tenant_id: str
) -> list[tuple[str, int]]:
    """The distinct fully-bucketed `(work_type, tier)` pairs that have
    at least one shadow decision. Unbucketable decisions (NULL
    work_type or tier) are excluded, matching the estimator's rule."""
    pairs = {
        (d.work_type, d.tier)
        for d in read_shadow_decisions(paths, tenant_id=tenant_id)
        if d.work_type is not None and d.tier is not None
    }
    return sorted(pairs)  # type: ignore[arg-type]


def calibrate(
    decisions: Iterable[ShadowDecision],
    regressed_card_ids: frozenset[str],
    *,
    work_type: str | None = None,
    tier: int | None = None,
    n_bands: int = DEFAULT_N_BANDS,
    window_cards: int | None = DEFAULT_WINDOW_CARDS,
) -> Calibration:
    """Band the decisions by confidence score and measure per-band
    regression rates. Pure.

    The window keeps the most recent `window_cards` cards by decision
    timestamp (after the latest-per-card collapse), approximating the
    spec's rolling window without needing wall-clock access."""
    if n_bands < 1:
        raise ValueError(f"n_bands must be >= 1, got {n_bands}")
    if window_cards is not None and window_cards < 1:
        raise ValueError(
            f"window_cards must be >= 1 or None, got {window_cards}"
        )
    latest = latest_per_card(decisions)
    latest.sort(key=lambda d: d.at)
    if window_cards is not None and len(latest) > window_cards:
        latest = latest[-window_cards:]

    width = 1.0 / n_bands
    counts = [0] * n_bands
    regressions = [0] * n_bands
    for d in latest:
        # The epsilon keeps boundary-exact scores in their own band:
        # 0.3 / 0.1 is 2.999... in binary floating point, which would
        # drop a score sitting exactly on a band edge (including 0.95
        # at 20 bands -- the auto threshold) one band too low.
        idx = min(int(d.confidence_score * n_bands + 1e-9), n_bands - 1)
        counts[idx] += 1
        if d.card_id in regressed_card_ids:
            regressions[idx] += 1

    bands: list[CalibrationBand] = []
    for idx in range(n_bands - 1, -1, -1):  # highest confidence first
        n = counts[idx]
        bands.append(CalibrationBand(
            lo=round(idx * width, 4),
            hi=round((idx + 1) * width, 4),
            n=n,
            regressions=regressions[idx],
            regression_rate=(regressions[idx] / n) if n else 0.0,
        ))

    populated = [b for b in bands if b.n > 0]
    monotonic = all(
        populated[i].regression_rate <= populated[i + 1].regression_rate
        for i in range(len(populated) - 1)
    )
    overall_n = len(latest)
    overall_regressed = sum(
        1 for d in latest if d.card_id in regressed_card_ids
    )
    return Calibration(
        work_type=work_type,
        tier=tier,
        bands=tuple(bands),
        monotonic=monotonic,
        overall_n=overall_n,
        overall_regressions=overall_regressed,
        overall_regression_rate=(
            overall_regressed / overall_n if overall_n else 0.0
        ),
    )


def calibration_for_bucket(
    store: MetricsStore,
    paths: RuntimePaths,
    *,
    tenant_id: str,
    work_type: str,
    tier: int,
    n_bands: int = DEFAULT_N_BANDS,
    window_cards: int | None = DEFAULT_WINDOW_CARDS,
) -> Calibration:
    """The spec 7.3 `calibration(...)` read for one bucket."""
    decisions = [
        d for d in read_shadow_decisions(paths, tenant_id=tenant_id)
        if d.work_type == work_type and d.tier == tier
    ]
    regressed = store.regressed_card_ids(
        tenant_id=tenant_id, work_type=work_type, tier=tier
    )
    return calibrate(
        decisions, regressed,
        work_type=work_type, tier=tier,
        n_bands=n_bands, window_cards=window_cards,
    )


def render_table(cal: Calibration) -> str:
    """The spec 8.2 calibration plot as fixed-width text."""
    bucket = (
        f"{cal.work_type}/tier{cal.tier}"
        if cal.work_type is not None and cal.tier is not None
        else "(unbucketed)"
    )
    lines = [
        f"bucket: {bucket}  n={cal.overall_n}  "
        f"regression_rate={cal.overall_regression_rate:.1%}  "
        f"calibration={'monotonic' if cal.monotonic else 'INVERTED'}",
        f"{'band':<16}{'n':>6}{'regressions':>14}{'rate':>9}",
    ]
    for band in cal.bands:
        if band.n == 0:
            continue
        closer = "]" if band.hi >= 1.0 else ")"
        lines.append(
            f"[{band.lo:.2f}, {band.hi:.2f}{closer:<2}"
            f"{band.n:>6}{band.regressions:>14}"
            f"{band.regression_rate:>8.1%}"
        )
    if cal.overall_n == 0:
        lines.append("(no shadow decisions in window)")
    return "\n".join(lines)


__all__ = [
    "Calibration",
    "CalibrationBand",
    "DEFAULT_N_BANDS",
    "DEFAULT_WINDOW_CARDS",
    "ShadowDecision",
    "buckets_in_shadow_log",
    "calibrate",
    "calibration_for_bucket",
    "latest_per_card",
    "read_shadow_decisions",
    "render_table",
    "shadow_decisions_from_events",
]
