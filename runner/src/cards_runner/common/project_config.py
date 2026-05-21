"""Per-project runtime configuration.

`DaemonConfig` carries the host-wide knobs that the operator sets at
daemon-start time (poll interval, max_parallel, gh path, ...). This
module carries the per-project knobs that a project's `project.yaml`
sets and the daemon may re-read mid-run. Splitting the two prevents a
project from cargo-culting a host-wide flag (and a forgotten host flag
from silently overriding a project's preference).

`ProjectConfig` is intentionally a small dataclass with `Optional`
fields. The daemon's existing modules pass their own defaults in; a
`None` here means "the project did not say, fall back to the daemon
default". This keeps the migration onto project config a single-file
plumbing change rather than a sweeping refactor.

Hot-reload model: the daemon polls the config file's mtime each tick
via `ProjectConfigLoader.reload_if_changed()` and replaces its
in-memory copy when the file has been written since the last load.
Hot-reload via SIGHUP would be ideal on Linux; Windows does not have
SIGHUP, so file-mtime polling is the portable approach. The polling
cost is one `stat()` per tick.

Per RUNNER_CONTRACT.md, the runner reserves these per-project knobs:

- `story_source_path` (eligibility / story-drift baseline)
- `subjective_cascade_disabled` (verifier opt-out)
- `cascade_escalation_threshold`, `verifier_skip_confidence_threshold`,
  `cascade_max_escalations` (templates/project_config.yaml)
- Sibling-reviewer identity (chunk 5)
- Amendment-reviewer identity (chunk 5)
- Per-project merge gate relaxation (chunk 5)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


log = logging.getLogger(__name__)


PROJECT_CONFIG_FILENAME: str = "project.yaml"


@dataclass(frozen=True)
class ReviewerConfig:
    """Per-reviewer agent identity and call shape.

    `model_id` is the Anthropic model the reviewer agent runs against.
    `label` is the string written into `verified_by` / `amended_by` on
    the card so the audit trail names the agent that reviewed it.
    `cost_cap_usd` caps the reviewer's spend per card; the reviewer
    refuses to start if it cannot run within the cap. `prompt_extra`
    is appended to the reviewer's system prompt -- a project that wants
    the reviewer to enforce a project-specific style guide threads its
    text here.
    """

    enabled: bool = False
    model_id: str = "claude-haiku-4-5-20251001"
    label: str = "cards-runner-reviewer"
    cost_cap_usd: float | None = 0.50
    prompt_extra: str = ""


@dataclass(frozen=True)
class MergeGateRelaxation:
    """Project-scoped merge gate overrides.

    Defaults preserve the contract's most-restrictive interpretation:
    tier 3-4 require a sibling reviewer, tier 5-6 / pinned go to a human.
    A project that wants to auto-merge tier-3 cards (because its CI is
    rigorous enough that a sibling review adds no value) flips
    `auto_merge_tier_3_4: true`. `pin_required: true` cards are never
    relaxed -- the contract is explicit that pin overrides relaxation.
    """

    auto_merge_tier_3_4: bool = False
    pr_base_branch: str | None = None  # overrides DaemonConfig default if set.


@dataclass(frozen=True)
class ProjectConfig:
    """One project's runtime knobs.

    Every field is optional. The daemon code reads through `value_or(...)`
    helpers so a `None` falls back to the host default. New fields are
    additive: ship them with a default and consumers ignore them until
    they opt in.
    """

    source_path: str = ""  # provenance string for logs; "" if defaults.

    # Verifier / cascade.
    subjective_cascade_disabled: bool | None = None
    verifier_skip_confidence_threshold: float | None = None
    cascade_escalation_threshold: float | None = None
    cascade_max_escalations: int | None = None

    # Story drift.
    story_source_path: str | None = None  # absolute path or project-relative.

    # Reviewers.
    sibling_reviewer: ReviewerConfig = field(default_factory=ReviewerConfig)
    amendment_reviewer: ReviewerConfig = field(default_factory=ReviewerConfig)

    # Merge gate.
    merge_gate: MergeGateRelaxation = field(default_factory=MergeGateRelaxation)

    @classmethod
    def default(cls) -> "ProjectConfig":
        """The empty config: every override is None, defaults apply."""
        return cls()


class ProjectConfigError(Exception):
    """Raised when a project.yaml is present but cannot be parsed."""


def load_project_config(path: Path, *, strict: bool = False) -> ProjectConfig:
    """Read `project.yaml` at `path` and return a `ProjectConfig`.

    A missing file returns the default config (every override `None`).
    `strict=True` raises `ProjectConfigError` instead so a CLI command
    that explicitly named the path can flag the typo.
    """
    if not path.is_file():
        if strict:
            raise ProjectConfigError(f"project config not found: {path}")
        return ProjectConfig.default()
    try:
        raw_text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text) or {}
    except (OSError, yaml.YAMLError) as exc:
        if strict:
            raise ProjectConfigError(
                f"could not read project config {path}: {exc}"
            ) from exc
        log.warning(
            "project config at %s unreadable (%s); using defaults", path, exc
        )
        return ProjectConfig.default()
    if not isinstance(data, dict):
        if strict:
            raise ProjectConfigError(
                f"project config {path} is not a YAML mapping at the root"
            )
        log.warning(
            "project config %s is not a mapping; using defaults", path
        )
        return ProjectConfig.default()
    return _from_dict(data, source_path=str(path))


def _from_dict(data: dict[str, Any], *, source_path: str) -> ProjectConfig:
    verifier = data.get("verifier") or {}
    cascade = data.get("cascade") or {}
    reviewers = data.get("reviewers") or {}
    merge = data.get("merge_gate") or {}
    return ProjectConfig(
        source_path=source_path,
        subjective_cascade_disabled=_opt_bool(
            verifier.get("subjective_cascade_disabled")
        ),
        verifier_skip_confidence_threshold=_opt_float(
            verifier.get("skip_confidence_threshold")
        ),
        cascade_escalation_threshold=_opt_float(
            cascade.get("escalation_threshold")
        ),
        cascade_max_escalations=_opt_int(
            cascade.get("max_escalations")
        ),
        story_source_path=_opt_str(data.get("story_source_path")),
        sibling_reviewer=_reviewer(reviewers.get("sibling")),
        amendment_reviewer=_reviewer(reviewers.get("amendment")),
        merge_gate=_merge_gate(merge),
    )


def _reviewer(value: Any) -> ReviewerConfig:
    if not isinstance(value, dict):
        return ReviewerConfig()
    return ReviewerConfig(
        enabled=bool(value.get("enabled", False)),
        model_id=str(value.get("model", "claude-haiku-4-5-20251001")),
        label=str(value.get("label", "cards-runner-reviewer")),
        cost_cap_usd=_opt_float(value.get("cost_cap_usd")) or 0.50,
        prompt_extra=str(value.get("prompt_extra") or ""),
    )


def _merge_gate(value: Any) -> MergeGateRelaxation:
    if not isinstance(value, dict):
        return MergeGateRelaxation()
    return MergeGateRelaxation(
        auto_merge_tier_3_4=bool(value.get("auto_merge_tier_3_4", False)),
        pr_base_branch=_opt_str(value.get("pr_base_branch")),
    )


# ---- coercion helpers ------------------------------------------------


def _opt_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "on"}
    return None


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


# ---- hot reload ------------------------------------------------------


class ProjectConfigLoader:
    """Caches one `ProjectConfig` and reloads on file change.

    The daemon constructs one loader at boot, calls `current()` whenever
    it needs the config, and calls `reload_if_changed()` once per tick.
    The polling cost is one `stat()` per tick; if the file has not
    changed since the last load the cached config is returned.

    Designed for cross-platform behavior: SIGHUP-style "reload now"
    plumbing would be nicer on Linux, but Windows has no SIGHUP and the
    file-mtime poll is the portable equivalent.
    """

    def __init__(
        self,
        path: Path | None,
        *,
        strict: bool = False,
    ) -> None:
        self._path = path
        self._strict = strict
        self._mtime: float | None = None
        if path is None:
            self._config = ProjectConfig.default()
        else:
            self._config = load_project_config(path, strict=strict)
            self._mtime = _mtime_or_none(path)

    @property
    def path(self) -> Path | None:
        return self._path

    def current(self) -> ProjectConfig:
        return self._config

    def reload_if_changed(self) -> bool:
        """Reload the config when the file's mtime has advanced.

        Returns True when the cached config was replaced. A missing path
        or a missing file returns False without raising; the daemon
        treats absence as "use defaults".
        """
        if self._path is None:
            return False
        current_mtime = _mtime_or_none(self._path)
        if current_mtime is None:
            # File was deleted since the last load. Keep the cached copy
            # so the daemon does not silently revert mid-run; an operator
            # explicitly removing project.yaml should also restart.
            return False
        if self._mtime is not None and current_mtime <= self._mtime:
            return False
        try:
            self._config = load_project_config(self._path, strict=self._strict)
        except ProjectConfigError as exc:
            log.error(
                "project config reload from %s failed: %s; keeping cached copy",
                self._path, exc,
            )
            return False
        self._mtime = current_mtime
        log.info("project config reloaded from %s", self._path)
        return True


def _mtime_or_none(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def resolve_project_config_path(
    explicit: Path | None,
    *,
    todo_root: Path,
) -> Path | None:
    """Pick where to read `project.yaml` from.

    Order: an explicit path (the CLI's `--project-config`), then
    `<todo_root>/project.yaml`. Returns None when neither is set; the
    daemon then runs with built-in defaults.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    candidate = (todo_root / PROJECT_CONFIG_FILENAME).resolve()
    return candidate if candidate.exists() else None
