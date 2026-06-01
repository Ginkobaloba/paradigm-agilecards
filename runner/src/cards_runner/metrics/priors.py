"""Cold-start prior loader for the estimator.

Loads `runner/templates/metrics_priors.yaml` (or an operator-supplied
override) into a typed dataclass and implements the spec's section
8.3 layered prior selection:

  1. Per-(work_type, tier) -- from a populated empirical bucket.
  2. Per-tier only         -- aggregate across work_types at that tier.
  3. Global cold-start     -- the YAML.

This module is responsible only for the YAML side. The orchestrator
(`recalibrate.py`) handles layers 1 and 2 by querying the live store
and falling through to `layered_prior(work_type, tier, priors)` when
neither has enough data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from .estimator import PercentileSet


# Sentinel for "no override on this work_type"; the orchestrator falls
# back to the YAML's top-level `shrinkage_k`. Distinct from `None`
# because a missing key and an explicit `null` carry the same meaning
# in YAML.
_NO_OVERRIDE: object = object()


# Path to the in-tree default priors file. Tests and operators may
# override via the `path` argument to `load_priors`.
#
# Resolution: this file lives at
# `runner/src/cards_runner/metrics/priors.py`. The templates dir is at
# `runner/templates/`, three parents up from the package dir.
DEFAULT_PRIORS_PATH: Path = (
    Path(__file__).resolve().parents[3] / "templates" / "metrics_priors.yaml"
)


@dataclass(frozen=True)
class PriorSet:
    """One tier's prior values.

    Mirrors the YAML schema and the `Estimate` shape. Optional fields
    let the operator omit a metric they don't care about; the
    estimator treats a missing field as "no prior available, skip
    this metric".
    """

    agent_wall_seconds: PercentileSet
    executor_tokens: PercentileSet
    human_review_wall_seconds: PercentileSet
    rework_rate_mean: float
    contract_survival_rate: float


@dataclass(frozen=True)
class Priors:
    """The full prior table loaded from YAML.

    `tiers` is keyed by integer tier (1-6). `shrinkage_k_overrides` is
    keyed by work_type; an entry with no override returns `shrinkage_k`.
    """

    version: int
    shrinkage_k: int
    tiers: dict[int, PriorSet] = field(default_factory=dict)
    shrinkage_k_overrides: dict[str, int] = field(default_factory=dict)

    def k_for(self, work_type: str) -> int:
        """The shrinkage constant for this work_type, defaulting to
        the global `shrinkage_k`."""
        return self.shrinkage_k_overrides.get(work_type, self.shrinkage_k)


class PriorsError(Exception):
    """Raised when the priors YAML cannot be parsed or is malformed."""


def load_priors(path: Path | None = None) -> Priors:
    """Read a priors YAML and return a typed `Priors`.

    Defaults to the in-tree template at `DEFAULT_PRIORS_PATH`. Raises
    `PriorsError` on malformed YAML, missing required keys, or values
    out of range. Operators wanting custom priors point the daemon
    config at their file; the YAML schema is the same.
    """
    target = path or DEFAULT_PRIORS_PATH
    if not target.is_file():
        raise PriorsError(f"priors file not found: {target}")
    try:
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PriorsError(f"malformed YAML at {target}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PriorsError(
            f"priors file must be a YAML mapping, got {type(loaded).__name__}"
        )
    return _build_priors(loaded, source=str(target))


def _build_priors(raw: dict[str, object], *, source: str) -> Priors:
    version = _require_int(raw, "version", source)
    shrinkage_k = _require_int(raw, "shrinkage_k", source)
    if shrinkage_k < 0:
        raise PriorsError(f"shrinkage_k must be non-negative at {source}")
    tiers_raw = raw.get("tiers")
    if not isinstance(tiers_raw, dict):
        raise PriorsError(f"`tiers` must be a mapping at {source}")
    tiers: dict[int, PriorSet] = {}
    for tier_key, tier_payload in tiers_raw.items():
        tier = _coerce_tier(tier_key, source)
        if not isinstance(tier_payload, dict):
            raise PriorsError(
                f"tier {tier} payload must be a mapping at {source}"
            )
        tiers[tier] = _build_prior_set(tier, tier_payload, source=source)
    if not tiers:
        raise PriorsError(f"priors file has no tiers at {source}")
    overrides_raw = raw.get("shrinkage_k_overrides") or {}
    if not isinstance(overrides_raw, dict):
        raise PriorsError(
            f"`shrinkage_k_overrides` must be a mapping at {source}"
        )
    overrides: dict[str, int] = {}
    for wt, value in overrides_raw.items():
        if not isinstance(value, int):
            raise PriorsError(
                f"shrinkage_k_overrides[{wt!r}] must be int at {source}"
            )
        if value < 0:
            raise PriorsError(
                f"shrinkage_k_overrides[{wt!r}] must be non-negative at {source}"
            )
        overrides[str(wt)] = value
    return Priors(
        version=version,
        shrinkage_k=shrinkage_k,
        tiers=tiers,
        shrinkage_k_overrides=overrides,
    )


def _build_prior_set(
    tier: int, payload: dict[str, object], *, source: str,
) -> PriorSet:
    return PriorSet(
        agent_wall_seconds=_require_percentiles(
            payload, "agent_wall_seconds", tier=tier, source=source,
        ),
        executor_tokens=_require_percentiles(
            payload, "executor_tokens", tier=tier, source=source,
        ),
        human_review_wall_seconds=_require_percentiles(
            payload, "human_review_wall_seconds", tier=tier, source=source,
        ),
        rework_rate_mean=_require_float(
            payload, "rework_rate_mean", tier=tier, source=source,
        ),
        contract_survival_rate=_require_float(
            payload, "contract_survival_rate", tier=tier, source=source,
        ),
    )


def _require_percentiles(
    payload: dict[str, object], metric: str, *, tier: int, source: str,
) -> PercentileSet:
    """Read a `<metric>_p50` / `_p75` / `_p90` triple from the YAML.

    The spec's `metric_estimates` schema (section 3.3) stores P75 only
    for `agent_wall_seconds`; `executor_tokens` and
    `human_review_wall_seconds` keep just P50 and P90. The prior YAML
    accepts either form: when `_p75` is omitted it's synthesized as
    the midpoint of P50 and P90. The estimator's blend math uses all
    three internally but the recalibrator only persists the metrics
    the spec lists, so the synthesized P75 never leaks into storage."""
    p50_key = f"{metric}_p50"
    p75_key = f"{metric}_p75"
    p90_key = f"{metric}_p90"
    p50 = _require_number(payload, p50_key, tier=tier, source=source)
    p90 = _require_number(payload, p90_key, tier=tier, source=source)
    if p75_key in payload:
        p75 = _require_number(payload, p75_key, tier=tier, source=source)
    else:
        p75 = (p50 + p90) / 2.0
    if not (p50 <= p75 <= p90):
        raise PriorsError(
            f"tier {tier} {metric} percentiles must be non-decreasing "
            f"(p50={p50}, p75={p75}, p90={p90}) at {source}"
        )
    return PercentileSet(p50=float(p50), p75=float(p75), p90=float(p90))


def _require_number(
    payload: dict[str, object], key: str, *, tier: int, source: str,
) -> float:
    value = payload.get(key, _NO_OVERRIDE)
    if value is _NO_OVERRIDE:
        raise PriorsError(f"tier {tier} missing `{key}` at {source}")
    if not isinstance(value, (int, float)):
        raise PriorsError(
            f"tier {tier} `{key}` must be a number at {source}, "
            f"got {type(value).__name__}"
        )
    if value < 0:
        raise PriorsError(f"tier {tier} `{key}` must be non-negative at {source}")
    return float(value)


def _require_int(raw: dict[str, object], key: str, source: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PriorsError(f"`{key}` must be int at {source}")
    return value


def _require_float(
    payload: dict[str, object], key: str, *, tier: int, source: str,
) -> float:
    value = payload.get(key, _NO_OVERRIDE)
    if value is _NO_OVERRIDE:
        raise PriorsError(f"tier {tier} missing `{key}` at {source}")
    if not isinstance(value, (int, float)):
        raise PriorsError(
            f"tier {tier} `{key}` must be a number at {source}, "
            f"got {type(value).__name__}"
        )
    if not 0.0 <= float(value) <= 1.0:
        raise PriorsError(
            f"tier {tier} `{key}` must be in [0,1] at {source}, got {value}"
        )
    return float(value)


def _coerce_tier(key: object, source: str) -> int:
    """YAML int keys parse as ints; some operators write them as strings."""
    if isinstance(key, bool):
        raise PriorsError(f"tier key must be int, got bool at {source}")
    if isinstance(key, int):
        tier = key
    elif isinstance(key, str):
        try:
            tier = int(key)
        except ValueError as exc:
            raise PriorsError(
                f"tier key {key!r} is not an integer at {source}"
            ) from exc
    else:
        raise PriorsError(
            f"tier key must be int or string-of-int at {source}, "
            f"got {type(key).__name__}"
        )
    if not 1 <= tier <= 6:
        raise PriorsError(f"tier {tier} out of range [1,6] at {source}")
    return tier


def layered_prior(
    tier: int,
    priors: Priors,
) -> PriorSet:
    """Return the global cold-start prior for `tier`.

    Layers 1 and 2 of the section 8.3 layered prior are
    orchestrator-side concerns (they query the live store); when both
    fall through, this is the fallback. The orchestrator calls this
    directly when the bucket and the tier-aggregate are both empty.

    Raises `PriorsError` if the tier is not in the YAML (an operator
    can omit tiers they don't use, but every actively-routed tier
    must have a prior).
    """
    prior = priors.tiers.get(tier)
    if prior is None:
        raise PriorsError(
            f"no cold-start prior for tier {tier}; add it to the priors YAML"
        )
    return prior


__all__ = [
    "DEFAULT_PRIORS_PATH",
    "PriorSet",
    "Priors",
    "PriorsError",
    "layered_prior",
    "load_priors",
]
