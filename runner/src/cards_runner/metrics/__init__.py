"""Throughput-metrics estimator + read APIs.

Lands the read side of `docs/design/throughput_metrics_ledger.md`. The
chunk 1 schema (`cards.work_type`, `card_metrics`, `metric_estimates`)
is the substrate this module operates on; the chunk 2 writer (the
per-lifecycle `card_metrics` population) is the eventual data source.
This package can ship and be useful before chunk 2 lands because the
cold-start priors in `runner/templates/metrics_priors.yaml` cover
empty / low-data buckets.

Exposed surface (chunk 3 ships only the recalibrator; quote/estimate/
survival/trust readers land in chunk 4):

- `Estimate` -- the public dataclass returned by the estimator.
- `recalibrate_all(repo)` -- read `card_metrics`, compute per-bucket
  estimates, write `metric_estimates`. Idempotent.
- `recalibrate_bucket(repo, work_type, tier)` -- the per-bucket entry
  point the calibration loop uses on terminal-state transitions.
- `load_priors(path=None)` -- read the cold-start YAML; defaults to
  the in-tree template.

Math (`estimator.py`) and prior layering (`priors.py`) are split out
so the chunk-4 read API consumes them directly without going through
the orchestrator.
"""
from __future__ import annotations

from .calibration import (
    Calibration,
    CalibrationBand,
    ShadowDecision,
    buckets_in_shadow_log,
    calibrate,
    calibration_for_bucket,
    read_shadow_decisions,
)
from .estimator import (
    Estimate,
    PercentileSet,
    blend,
    percentiles_from_samples,
)
from .events import (
    MetricsEvent,
    append_event,
    events_path,
    read_events,
    read_events_for_card,
)
from .priors import (
    PriorSet,
    Priors,
    layered_prior,
    load_priors,
)
from .ramp import (
    PhaseRecommendation,
    RampState,
    RampStore,
    count_live_decisions,
    evaluate_advance,
    killswitch_quiet,
)
from .recalibrate import (
    RecalibrationResult,
    recalibrate_all,
    recalibrate_bucket,
)
from .store import CardMetricsFullRow, MetricsStore
from .writer import LedgerWriter, fold_events

__all__ = [
    "Calibration",
    "CalibrationBand",
    "CardMetricsFullRow",
    "Estimate",
    "LedgerWriter",
    "MetricsEvent",
    "MetricsStore",
    "PercentileSet",
    "PhaseRecommendation",
    "Priors",
    "PriorSet",
    "RampState",
    "RampStore",
    "RecalibrationResult",
    "ShadowDecision",
    "append_event",
    "blend",
    "buckets_in_shadow_log",
    "calibrate",
    "calibration_for_bucket",
    "count_live_decisions",
    "evaluate_advance",
    "events_path",
    "fold_events",
    "killswitch_quiet",
    "layered_prior",
    "load_priors",
    "percentiles_from_samples",
    "read_events",
    "read_events_for_card",
    "read_shadow_decisions",
    "recalibrate_all",
    "recalibrate_bucket",
]
