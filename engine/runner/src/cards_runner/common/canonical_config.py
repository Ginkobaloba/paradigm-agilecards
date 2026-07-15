"""Canonical /cards skill config loaders.

The /cards skill ships two YAML files at the repo root that the runner
needs to consult: `tier_map_claude.yaml` (points 1-6 -> model + thinking +
model_floor + pin_required) and `tier_pricing.yaml` (model id -> per-million
input/output USD rates). Until chunk 4 the runner duplicated those tables
inline (`_POINTS_TO_TIER` in `sdk_invoker.py`, `_DEFAULT_PRICING` in
`cost.py`); this module reads the canonical files so the runner and the
planner cannot drift apart.

Path resolution, in order:

1. Explicit `path=` argument (callers can wire a project config).
2. Env var (`CARDS_RUNNER_TIER_MAP_YAML` / `CARDS_RUNNER_TIER_PRICING_YAML`).
3. Walk up from the runner package's parent dirs looking for the named
   file at the project root. The runner repo layout is::

       repo_root/
         tier_map_claude.yaml      <- canonical
         tier_pricing.yaml         <- canonical
         runner/src/cards_runner/  <- this package

   So three `parent` hops from `cards_runner/common/canonical_config.py`
   land at `repo_root/` in a development checkout. Production installs
   may not have the YAMLs alongside; the env var is the deployment hook.
4. Fall back to the embedded defaults (the same values the chunk-3
   stand-ins carried). The fall back is opt-in: callers that want to
   detect a missing YAML can pass `strict=True` and catch
   `CanonicalConfigMissing` instead.

USD pricing is keyed by Anthropic model id in the YAML, not by coarse
tier. `Pricing` (worker_stub.cost) keeps a tier-keyed table because the
cost-cap math runs over the cascade tier; this loader reduces the
model-keyed YAML to a tier-keyed table using `_model_to_tier`, which is
the same substring match `model_tier()` uses elsewhere. A YAML missing
an entry for one tier keeps that tier's embedded default rather than
crashing the worker (a wrong rate only shifts where the cap trips; a
missing rate would be a runtime crash).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml


log = logging.getLogger(__name__)


# Embedded fallbacks. These are the values chunk 3 carried inline; they
# survive as the last-resort fallback when neither the canonical YAML
# nor an env override is reachable. Keep them in sync with the canonical
# files at major Anthropic family bumps.
_EMBEDDED_TIER_MAP: Final[dict[int, dict[str, Any]]] = {
    1: {"model": "claude-haiku-4-5-20251001", "model_floor": "haiku",
        "pin_required": False, "extended_thinking": False},
    2: {"model": "claude-haiku-4-5-20251001", "model_floor": "haiku",
        "pin_required": False, "extended_thinking": True},
    3: {"model": "claude-sonnet-4-6", "model_floor": "sonnet",
        "pin_required": False, "extended_thinking": False},
    4: {"model": "claude-sonnet-4-6", "model_floor": "sonnet",
        "pin_required": False, "extended_thinking": True},
    5: {"model": "claude-opus-4-7", "model_floor": "opus",
        "pin_required": True, "extended_thinking": False},
    6: {"model": "claude-opus-4-7", "model_floor": "opus",
        "pin_required": True, "extended_thinking": True},
}

# Self-hosted local models (KL1) map to a fixed model id served over an
# OpenAI-compatible endpoint. Kept minimal: the shipped tier_map_local.yaml
# is the source of truth; this is only the last-resort fallback if that
# file is dropped from a deploy, and it must NOT silently route a local
# request to a paid Claude model.
_EMBEDDED_TIER_MAP_LOCAL: Final[dict[int, dict[str, Any]]] = {
    p: {
        "model": "ollama/qwen3:30b",
        "model_floor": "local",
        "pin_required": p >= 5,
        "extended_thinking": p % 2 == 0,
    }
    for p in range(1, 7)
}

_EMBEDDED_TIER_MAPS: Final[dict[str, dict[int, dict[str, Any]]]] = {
    "claude": _EMBEDDED_TIER_MAP,
    "local": _EMBEDDED_TIER_MAP_LOCAL,
}


_EMBEDDED_PRICING_BY_TIER: Final[dict[str, tuple[float, float]]] = {
    "local": (0.00, 0.00),  # self-hosted inference is free.
    "haiku": (1.00, 5.00),
    "sonnet": (3.00, 15.00),
    "opus": (15.00, 75.00),
}


_TIER_ORDER: Final[tuple[str, ...]] = ("haiku", "sonnet", "opus")


# Local (self-hosted) models carry a `<provider>/<tag>` prefix so the
# runner can distinguish them from hosted models. They always price at
# the zero-rate `local` tier regardless of what the tag contains.
LOCAL_MODEL_PREFIXES: Final[tuple[str, ...]] = ("ollama/", "local/", "vllm/")


def is_local_model(model_id: str) -> bool:
    """True if `model_id` is a self-hosted local model.

    Local models are namespaced with a provider prefix (`ollama/`,
    `local/`, `vllm/`). This is how the cost governor and tier logic
    tell "free, runs on my own GPU" from "paid, runs in the cloud".

    The bare token `"local"` is also recognized: it is the local-tier
    sentinel used as a `model_floor`, so floor normalization via
    `model_tier` maps it to the `local` tier instead of mis-clamping a
    local card up to the `opus` floor.
    """
    low = model_id.lower()
    if low == "local":
        return True
    return any(low.startswith(prefix) for prefix in LOCAL_MODEL_PREFIXES)


# Env vars used by the loader and by callers that want to opt in.
ENV_TIER_MAP_PATH: Final[str] = "CARDS_RUNNER_TIER_MAP_YAML"
ENV_TIER_PRICING_PATH: Final[str] = "CARDS_RUNNER_TIER_PRICING_YAML"


class CanonicalConfigMissing(RuntimeError):
    """Raised when `strict=True` and no canonical YAML can be found."""


@dataclass(frozen=True)
class TierMap:
    """The planner's `tier_map_claude.yaml`, lifted into runner-friendly form.

    The contract pegs the planner-vs-runner shared vocabulary on `points`
    (1-6). Every accessor here is `points`-keyed; mapping points to a
    cascade-friendly tier string (`haiku|sonnet|opus`) is done at lookup
    time so the same `TierMap` is usable both by the executor (model id
    + extended thinking) and by the merge gate (pin override).
    """

    tiers: dict[int, dict[str, Any]]
    source: str  # human-readable provenance string for logs and reports.

    def model_for(self, points: int) -> str:
        return str(self._tier_dict(points)["model"])

    def model_floor_for(self, points: int) -> str:
        return str(self._tier_dict(points).get("model_floor") or "haiku").lower()

    def pin_required_for(self, points: int) -> bool:
        return bool(self._tier_dict(points).get("pin_required", False))

    def extended_thinking_for(self, points: int) -> bool:
        return bool(self._tier_dict(points).get("extended_thinking", False))

    def tier_name_for(self, points: int) -> str:
        """Coarse cascade tier (`haiku|sonnet|opus`) for a points value."""
        return _model_to_tier(self.model_for(points))

    def points_to_tier_map(self) -> dict[int, str]:
        return {p: self.tier_name_for(p) for p in sorted(self.tiers)}

    def points_to_model_map(self) -> dict[int, str]:
        return {p: self.model_for(p) for p in sorted(self.tiers)}

    def _tier_dict(self, points: int) -> dict[str, Any]:
        if points in self.tiers:
            return self.tiers[points]
        # Bounded fallback: clamp to the nearest defined tier rather than
        # crashing a planner that wrote out-of-range points.
        keys = sorted(self.tiers)
        clamped = min(max(points, keys[0]), keys[-1])
        return self.tiers[clamped]


@dataclass(frozen=True)
class TierPricing:
    """The planner's `tier_pricing.yaml` reduced to tier-keyed rates.

    The YAML is model-id-keyed (`claude-haiku-4-5-...: {input: 1.00, ...}`);
    this dataclass reduces it to the coarse-tier table the cost governor
    consumes. Both views are kept on the object so callers that want
    model-precise rates (a future per-model meter) can read those too.
    """

    by_tier: dict[str, tuple[float, float]]  # tier -> (input, output) $/Mtok.
    by_model: dict[str, tuple[float, float]]  # model id -> (input, output).
    source: str

    def rate_for_tier(self, tier: str) -> tuple[float, float]:
        return self.by_tier.get(tier.lower(), self.by_tier["opus"])

    def rate_for_model(self, model_id: str) -> tuple[float, float] | None:
        return self.by_model.get(model_id)


def _model_to_tier(model_id: str) -> str:
    """Substring match a model id to a coarse cascade tier.

    Mirrors `worker_stub.cost.model_tier` but kept private here so the
    canonical loader has no inbound dependency on the cost module. A
    cycle would otherwise emerge: cost imports the loader, the loader
    imports cost.
    """
    if is_local_model(model_id):
        return "local"
    low = model_id.lower()
    for tier in _TIER_ORDER:
        if tier in low:
            return tier
    return "opus"  # conservative default for unknown models.


def _candidate_paths(env_var: str, filename: str, explicit: Path | None) -> list[Path]:
    paths: list[Path] = []
    if explicit is not None:
        paths.append(Path(explicit).expanduser())
    env_value = os.environ.get(env_var)
    if env_value:
        paths.append(Path(env_value).expanduser())
    # Walk up from this file looking for the canonical name. Three hops
    # from `runner/src/cards_runner/common/canonical_config.py` is the
    # repo root in a dev checkout; we also try parents one and two above
    # that so a vendored layout under another root still resolves.
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        paths.append(ancestor / filename)
        # Stop at /; appending parents only matters down to the root.
        if ancestor == ancestor.parent:
            break
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        unique.append(p)
    return unique


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


def _read_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"canonical YAML at {path} must be a mapping; got {type(data).__name__}"
        )
    return data


def load_tier_map(
    path: Path | None = None, *, provider: str = "claude", strict: bool = False
) -> TierMap:
    """Load `tier_map_<provider>.yaml`. Falls back to embedded defaults.

    `provider` selects which canonical map to resolve (`claude` is the
    default and preserves prior behavior; `local` resolves the KL1
    local-GPU map). The embedded fallback is provider-matched so a
    missing `tier_map_local.yaml` never silently routes a local request
    to a paid Claude model.

    Set `strict=True` to raise `CanonicalConfigMissing` when no YAML is
    reachable -- useful in CI to catch a botched deploy that drops the
    canonical files.
    """
    provider = provider.strip().lower()
    filename = f"tier_map_{provider}.yaml"
    # An unknown/typo'd provider with a missing YAML falls back to the
    # FREE local map, never the paid Claude map: a misconfiguration must
    # not silently bill. `claude` is the one known paid map.
    embedded = _EMBEDDED_TIER_MAPS.get(provider, _EMBEDDED_TIER_MAP_LOCAL)
    candidates = _candidate_paths(ENV_TIER_MAP_PATH, filename, path)
    found = _first_existing(candidates)
    if found is None:
        if strict:
            raise CanonicalConfigMissing(
                f"no {filename} found on the canonical search path; "
                f"checked: {[str(p) for p in candidates]}"
            )
        log.info("%s not found; using embedded defaults", filename)
        return TierMap(tiers=dict(embedded), source="embedded-defaults")
    try:
        raw = _read_yaml(found)
        tiers_section = raw.get("tiers") if isinstance(raw, dict) else None
        if not isinstance(tiers_section, dict):
            raise ValueError("missing or non-dict `tiers:` section")
        tiers: dict[int, dict[str, Any]] = {}
        for key, value in tiers_section.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            tiers[idx] = dict(value)
        if not tiers:
            raise ValueError("tier_map YAML had no parseable entries")
    except Exception as exc:  # noqa: BLE001 - degrade gracefully.
        if strict:
            raise CanonicalConfigMissing(f"could not load {found}: {exc}") from exc
        log.warning("could not load %s (%s); using embedded defaults", found, exc)
        return TierMap(tiers=dict(embedded), source=f"embedded-defaults (after {found} failed)")
    return TierMap(tiers=tiers, source=str(found))


def load_tier_pricing(
    path: Path | None = None, *, strict: bool = False
) -> TierPricing:
    """Load `tier_pricing.yaml`. Falls back to embedded defaults per tier.

    The YAML is model-id keyed. This loader reduces it to a tier-keyed
    rate table (the cost governor's input). When the YAML lacks an entry
    for a tier, the embedded default fills it -- a missing rate is not
    a fatal error, only a less-precise cap.
    """
    candidates = _candidate_paths(ENV_TIER_PRICING_PATH, "tier_pricing.yaml", path)
    found = _first_existing(candidates)
    if found is None:
        if strict:
            raise CanonicalConfigMissing(
                "no tier_pricing.yaml found on the canonical search path; "
                f"checked: {[str(p) for p in candidates]}"
            )
        log.info("tier_pricing.yaml not found; using embedded defaults")
        return TierPricing(
            by_tier=dict(_EMBEDDED_PRICING_BY_TIER),
            by_model={},
            source="embedded-defaults",
        )
    try:
        raw = _read_yaml(found)
        by_model: dict[str, tuple[float, float]] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            if "input" not in value or "output" not in value:
                continue
            try:
                by_model[str(key)] = (float(value["input"]), float(value["output"]))
            except (TypeError, ValueError):
                continue
        if not by_model:
            raise ValueError("pricing YAML had no parseable model entries")
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise CanonicalConfigMissing(f"could not load {found}: {exc}") from exc
        log.warning("could not load %s (%s); using embedded defaults", found, exc)
        return TierPricing(
            by_tier=dict(_EMBEDDED_PRICING_BY_TIER),
            by_model={},
            source=f"embedded-defaults (after {found} failed)",
        )
    by_tier = dict(_EMBEDDED_PRICING_BY_TIER)
    for model_id, rates in by_model.items():
        by_tier[_model_to_tier(model_id)] = rates
    return TierPricing(by_tier=by_tier, by_model=by_model, source=str(found))
