"""Project config view used by the verifier.

The runner reads `.cards-config.yaml` and constructs one of these.
Handlers receive it on every dispatch. Only the fields the verifier
itself reads live here; the planner reads other fields from the same
file via its own config object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProjectConfig:
    """Verifier-relevant subset of project_config.yaml.

    Defaults match `templates/project_config.yaml` so a project with
    no `.cards-config.yaml` at all gets the documented defaults
    without the runner having to special-case missing files.
    """

    # Network gating.
    network_checks_allowed: bool = False

    # Subjective cascade knobs (v1.3 locked answers).
    subjective_starting_tier: str = "haiku"
    subjective_max_tier: str = "opus"
    subjective_confidence_threshold: float = 0.85
    subjective_cascade_disabled: bool = False

    # Per-type timeout overrides (project-wide). Item-level
    # `timeout_sec` wins over this; this wins over the per-type
    # default in `verifier.types.DEFAULT_TIMEOUTS_SEC`.
    type_timeout_overrides_sec: dict[str, int] = field(default_factory=dict)

    # Per-project additional credential vars to clear from the
    # command-handler scrubbed env baseline. Appended to the built-in
    # list. Lowercase comparison; prefix and exact match supported via
    # explicit prefix wildcard (e.g., "ACME_*").
    additional_env_to_scrub: tuple[str, ...] = ()

    # Per-project additional env vars to preserve in the baseline
    # (e.g., a CI-flag variant the project's tooling uses). Mapped
    # to the existing env scrub policy at handler load time.
    additional_env_to_preserve: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ProjectConfig":
        """Construct from a parsed YAML dict, tolerant of missing keys.

        Unknown keys are ignored on the verifier side; the planner's
        own config object is responsible for surfacing planner-side
        unknowns.
        """
        if raw is None:
            return cls()
        return cls(
            network_checks_allowed=bool(raw.get("network_checks_allowed", False)),
            subjective_starting_tier=str(
                raw.get("subjective_starting_tier", "haiku")
            ),
            subjective_max_tier=str(raw.get("subjective_max_tier", "opus")),
            subjective_confidence_threshold=float(
                raw.get("subjective_confidence_threshold", 0.85)
            ),
            subjective_cascade_disabled=bool(
                raw.get("subjective_cascade_disabled", False)
            ),
            type_timeout_overrides_sec=dict(
                raw.get("type_timeout_overrides_sec", {}) or {}
            ),
            additional_env_to_scrub=tuple(
                raw.get("additional_env_to_scrub", []) or []
            ),
            additional_env_to_preserve=tuple(
                raw.get("additional_env_to_preserve", []) or []
            ),
        )
