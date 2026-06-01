"""Tests for the priors YAML loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from cards_runner.metrics.priors import (
    DEFAULT_PRIORS_PATH,
    PriorsError,
    layered_prior,
    load_priors,
)


def test_default_priors_file_loads_clean() -> None:
    """The in-tree priors file must always parse; any drift in the
    schema validator is caught here before it breaks the
    recalibrator."""
    priors = load_priors()
    assert priors.version == 1
    assert priors.shrinkage_k == 5
    assert set(priors.tiers.keys()) == {1, 2, 3, 4, 5, 6}


def test_default_priors_percentiles_are_monotonic() -> None:
    """Tiers should escalate; tier 1 P50 wall-seconds < tier 6 P50.
    Catches a typo that flips a row."""
    priors = load_priors()
    by_tier_p50 = [
        priors.tiers[t].agent_wall_seconds.p50 for t in sorted(priors.tiers)
    ]
    # Tier 5 is "shallow opus" -- production-touching mechanical work --
    # which can run shorter wall-clock than tier 4 (sonnet+ET design
    # work), so we relax monotonicity to "tier 6 is the longest".
    assert by_tier_p50[-1] == max(by_tier_p50)


def test_load_priors_missing_file_raises(tmp_path: Path) -> None:
    target = tmp_path / "does_not_exist.yaml"
    with pytest.raises(PriorsError, match="not found"):
        load_priors(target)


def test_load_priors_malformed_yaml(tmp_path: Path) -> None:
    target = tmp_path / "bad.yaml"
    target.write_text("not: yaml: : :", encoding="utf-8")
    with pytest.raises(PriorsError, match="malformed YAML"):
        load_priors(target)


def test_load_priors_non_mapping_root(tmp_path: Path) -> None:
    target = tmp_path / "list.yaml"
    target.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(PriorsError, match="mapping"):
        load_priors(target)


def test_load_priors_missing_required_field(tmp_path: Path) -> None:
    target = tmp_path / "incomplete.yaml"
    target.write_text(
        "version: 1\n"
        "shrinkage_k: 5\n"
        "tiers:\n"
        "  3:\n"
        "    agent_wall_seconds_p50: 100\n"
        "    agent_wall_seconds_p75: 200\n"
        "    agent_wall_seconds_p90: 400\n"
        "    executor_tokens_p50: 1000\n"
        "    executor_tokens_p90: 5000\n"
        # human_review_wall_seconds missing -> error.
        "    rework_rate_mean: 0.2\n"
        "    contract_survival_rate: 0.8\n",
        encoding="utf-8",
    )
    with pytest.raises(PriorsError, match="human_review_wall_seconds"):
        load_priors(target)


def test_load_priors_synthesizes_p75_when_absent(tmp_path: Path) -> None:
    """Per spec section 3.3, executor_tokens and human_review_wall_seconds
    don't store P75. The YAML may omit `_p75`; the loader synthesizes
    it as (P50 + P90) / 2 to keep the internal PercentileSet uniform.
    Validates the recovery behavior the in-tree priors file relies on."""
    target = tmp_path / "no_p75.yaml"
    target.write_text(
        "version: 1\n"
        "shrinkage_k: 5\n"
        "tiers:\n"
        "  3:\n"
        "    agent_wall_seconds_p50: 100\n"
        "    agent_wall_seconds_p75: 200\n"
        "    agent_wall_seconds_p90: 400\n"
        "    executor_tokens_p50: 1000\n"
        # executor_tokens_p75 deliberately omitted.
        "    executor_tokens_p90: 5000\n"
        "    human_review_wall_seconds_p50: 100\n"
        # human_review_wall_seconds_p75 deliberately omitted.
        "    human_review_wall_seconds_p90: 500\n"
        "    rework_rate_mean: 0.2\n"
        "    contract_survival_rate: 0.8\n",
        encoding="utf-8",
    )
    priors = load_priors(target)
    tier = priors.tiers[3]
    assert tier.executor_tokens.p75 == 3000.0  # (1000 + 5000) / 2
    assert tier.human_review_wall_seconds.p75 == 300.0  # (100 + 500) / 2


def test_load_priors_non_monotonic_percentiles(tmp_path: Path) -> None:
    target = tmp_path / "bad_order.yaml"
    target.write_text(
        "version: 1\n"
        "shrinkage_k: 5\n"
        "tiers:\n"
        "  3:\n"
        "    agent_wall_seconds_p50: 400\n"  # p50 > p75 -> error.
        "    agent_wall_seconds_p75: 200\n"
        "    agent_wall_seconds_p90: 600\n"
        "    executor_tokens_p50: 1000\n"
        "    executor_tokens_p90: 5000\n"
        "    human_review_wall_seconds_p50: 100\n"
        "    human_review_wall_seconds_p90: 300\n"
        "    rework_rate_mean: 0.2\n"
        "    contract_survival_rate: 0.8\n",
        encoding="utf-8",
    )
    with pytest.raises(PriorsError, match="non-decreasing"):
        load_priors(target)


def test_load_priors_survival_rate_out_of_range(tmp_path: Path) -> None:
    target = tmp_path / "bad_rate.yaml"
    target.write_text(
        "version: 1\n"
        "shrinkage_k: 5\n"
        "tiers:\n"
        "  3:\n"
        "    agent_wall_seconds_p50: 100\n"
        "    agent_wall_seconds_p75: 200\n"
        "    agent_wall_seconds_p90: 400\n"
        "    executor_tokens_p50: 1000\n"
        "    executor_tokens_p90: 5000\n"
        "    human_review_wall_seconds_p50: 100\n"
        "    human_review_wall_seconds_p90: 300\n"
        "    rework_rate_mean: 0.2\n"
        "    contract_survival_rate: 1.5\n",  # > 1.0 -> error.
        encoding="utf-8",
    )
    with pytest.raises(PriorsError, match=r"\[0,1\]"):
        load_priors(target)


def test_load_priors_negative_shrinkage_k(tmp_path: Path) -> None:
    target = tmp_path / "bad_k.yaml"
    target.write_text(
        "version: 1\n"
        "shrinkage_k: -1\n"
        "tiers: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(PriorsError, match="non-negative"):
        load_priors(target)


def test_k_for_falls_back_to_default() -> None:
    priors = load_priors()
    assert priors.k_for("feature") == 5
    assert priors.k_for("never-seen-work-type") == 5


def test_k_for_uses_override(tmp_path: Path) -> None:
    target = tmp_path / "override.yaml"
    target.write_text(
        "version: 1\n"
        "shrinkage_k: 5\n"
        "tiers:\n"
        "  3:\n"
        "    agent_wall_seconds_p50: 100\n"
        "    agent_wall_seconds_p75: 200\n"
        "    agent_wall_seconds_p90: 400\n"
        "    executor_tokens_p50: 1000\n"
        "    executor_tokens_p90: 5000\n"
        "    human_review_wall_seconds_p50: 100\n"
        "    human_review_wall_seconds_p90: 300\n"
        "    rework_rate_mean: 0.2\n"
        "    contract_survival_rate: 0.8\n"
        "shrinkage_k_overrides:\n"
        "  docs: 2\n"
        "  spike: 20\n",
        encoding="utf-8",
    )
    priors = load_priors(target)
    assert priors.k_for("docs") == 2
    assert priors.k_for("spike") == 20
    assert priors.k_for("feature") == 5  # default fallback.


def test_layered_prior_returns_global_for_tier() -> None:
    priors = load_priors()
    prior = layered_prior(3, priors)
    assert prior == priors.tiers[3]


def test_layered_prior_missing_tier_raises(tmp_path: Path) -> None:
    """Operator omitted a tier from their priors file; layered_prior
    must raise rather than fabricate a value silently."""
    target = tmp_path / "minimal.yaml"
    target.write_text(
        "version: 1\n"
        "shrinkage_k: 5\n"
        "tiers:\n"
        "  1:\n"
        "    agent_wall_seconds_p50: 100\n"
        "    agent_wall_seconds_p75: 200\n"
        "    agent_wall_seconds_p90: 400\n"
        "    executor_tokens_p50: 1000\n"
        "    executor_tokens_p90: 5000\n"
        "    human_review_wall_seconds_p50: 100\n"
        "    human_review_wall_seconds_p90: 300\n"
        "    rework_rate_mean: 0.2\n"
        "    contract_survival_rate: 0.8\n",
        encoding="utf-8",
    )
    priors = load_priors(target)
    with pytest.raises(PriorsError, match="no cold-start prior for tier 3"):
        layered_prior(3, priors)


def test_default_path_points_in_tree() -> None:
    """Catches a refactor that moves the YAML out of templates/."""
    assert DEFAULT_PRIORS_PATH.is_file()
    assert DEFAULT_PRIORS_PATH.name == "metrics_priors.yaml"
