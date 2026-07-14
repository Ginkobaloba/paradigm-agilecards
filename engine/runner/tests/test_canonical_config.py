"""Tests for `cards_runner.common.canonical_config`.

The loader resolves the /cards skill's `tier_map_claude.yaml` and
`tier_pricing.yaml` from three sources -- explicit path, env var, and an
ancestor-walk from the package -- with embedded defaults as a final
fallback. Tests cover each resolution branch, the strict mode that
elevates a missing file to a runtime error, the malformed-YAML
degradation path, and the tier/model accessors callers rely on.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from cards_runner.common import canonical_config as cc


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# ---- tier map ---------------------------------------------------------


def test_load_tier_map_explicit_path(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "tier_map_claude.yaml",
        """
        version: 1
        tiers:
          1:
            model: claude-haiku-4-5-20251001
            model_floor: haiku
            pin_required: false
            extended_thinking: false
          5:
            model: claude-opus-4-7
            model_floor: opus
            pin_required: true
            extended_thinking: false
        """,
    )
    tier_map = cc.load_tier_map(path=path)
    assert tier_map.source == str(path)
    assert tier_map.model_for(1) == "claude-haiku-4-5-20251001"
    assert tier_map.model_floor_for(5) == "opus"
    assert tier_map.pin_required_for(5) is True
    assert tier_map.pin_required_for(1) is False
    assert tier_map.tier_name_for(1) == "haiku"
    assert tier_map.tier_name_for(5) == "opus"


def test_load_tier_map_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(
        tmp_path / "alt.yaml",
        """
        tiers:
          3:
            model: claude-sonnet-4-6
            pin_required: false
        """,
    )
    monkeypatch.setenv(cc.ENV_TIER_MAP_PATH, str(path))
    tier_map = cc.load_tier_map()
    assert tier_map.source == str(path)
    assert tier_map.model_for(3) == "claude-sonnet-4-6"


def test_load_tier_map_walks_up_from_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cc.ENV_TIER_MAP_PATH, raising=False)
    # The runner repo ships tier_map_claude.yaml at the repo root; an
    # ancestor walk from this package finds it without configuration.
    tier_map = cc.load_tier_map()
    assert tier_map.source.endswith("tier_map_claude.yaml")
    # Every points value 1..6 must be present in the shipped file.
    for points in range(1, 7):
        assert tier_map.model_for(points)
        assert tier_map.tier_name_for(points) in {"haiku", "sonnet", "opus"}


def test_load_tier_map_strict_raises_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the env var at a non-existent path and confirm strict mode
    # surfaces the failure instead of silently falling back.
    monkeypatch.setenv(cc.ENV_TIER_MAP_PATH, str(tmp_path / "does-not-exist.yaml"))
    # Walking up from the package would still find the shipped YAML, so
    # to genuinely simulate "missing" we monkey-patch the search path.
    monkeypatch.setattr(
        cc, "_candidate_paths",
        lambda env_var, filename, explicit: [tmp_path / "does-not-exist.yaml"],
    )
    with pytest.raises(cc.CanonicalConfigMissing):
        cc.load_tier_map(strict=True)


def test_load_tier_map_falls_back_on_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A malformed YAML must NOT crash the runner; the loader degrades to
    # the embedded defaults with a warning.
    bad = _write(tmp_path / "tier_map_claude.yaml", "tiers: 'not-a-dict'")
    monkeypatch.setattr(
        cc, "_candidate_paths", lambda env_var, filename, explicit: [bad]
    )
    tier_map = cc.load_tier_map()
    assert "embedded-defaults" in tier_map.source
    assert tier_map.model_for(1).startswith("claude-haiku")


def test_load_tier_map_out_of_range_points_clamps(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "tier_map_claude.yaml",
        """
        tiers:
          1:
            model: claude-haiku-4-5-20251001
            pin_required: false
          6:
            model: claude-opus-4-7
            pin_required: true
        """,
    )
    tier_map = cc.load_tier_map(path=path)
    # Below the defined range clamps to the lowest tier.
    assert tier_map.model_for(0).startswith("claude-haiku")
    # Above clamps to the highest.
    assert tier_map.model_for(99).startswith("claude-opus")


# ---- tier pricing -----------------------------------------------------


def test_load_tier_pricing_explicit_path(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "tier_pricing.yaml",
        """
        claude-haiku-4-5-20251001:
          input: 0.5
          output: 2.5
        claude-sonnet-4-6:
          input: 2.0
          output: 10.0
        claude-opus-4-7:
          input: 12.0
          output: 60.0
        """,
    )
    pricing = cc.load_tier_pricing(path=path)
    assert pricing.source == str(path)
    assert pricing.by_tier["haiku"] == (0.5, 2.5)
    assert pricing.by_tier["sonnet"] == (2.0, 10.0)
    assert pricing.by_tier["opus"] == (12.0, 60.0)
    assert pricing.rate_for_model("claude-sonnet-4-6") == (2.0, 10.0)
    assert pricing.rate_for_model("unknown-model") is None


def test_load_tier_pricing_env_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path / "alt_pricing.yaml",
        """
        claude-haiku-4-5-20251001:
          input: 0.01
          output: 0.02
        """,
    )
    monkeypatch.setenv(cc.ENV_TIER_PRICING_PATH, str(path))
    pricing = cc.load_tier_pricing()
    assert pricing.by_tier["haiku"] == (0.01, 0.02)
    # Other tiers fall back to embedded defaults.
    assert pricing.by_tier["sonnet"] == (3.00, 15.00)


def test_load_tier_pricing_falls_back_on_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cc, "_candidate_paths",
        lambda env_var, filename, explicit: [tmp_path / "missing.yaml"],
    )
    pricing = cc.load_tier_pricing()
    assert "embedded-defaults" in pricing.source
    assert pricing.by_tier["haiku"] == (1.00, 5.00)
    assert pricing.by_tier["opus"] == (15.00, 75.00)


def test_load_tier_pricing_strict_raises_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cc, "_candidate_paths",
        lambda env_var, filename, explicit: [tmp_path / "missing.yaml"],
    )
    with pytest.raises(cc.CanonicalConfigMissing):
        cc.load_tier_pricing(strict=True)


def test_load_tier_pricing_unknown_model_falls_to_opus_tier(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "tier_pricing.yaml",
        """
        weird-future-model:
          input: 99.0
          output: 999.0
        """,
    )
    pricing = cc.load_tier_pricing(path=path)
    # The model id has no tier substring; it slots into the 'opus'
    # bucket (the conservative default) and overrides the embedded
    # opus row.
    assert pricing.by_tier["opus"] == (99.0, 999.0)
    # The other tiers retained embedded defaults.
    assert pricing.by_tier["haiku"] == (1.00, 5.00)


def test_load_tier_pricing_walks_up_from_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(cc.ENV_TIER_PRICING_PATH, raising=False)
    pricing = cc.load_tier_pricing()
    assert pricing.source.endswith("tier_pricing.yaml")
    # The shipped file declares at least the three Claude tiers.
    for tier in ("haiku", "sonnet", "opus"):
        assert tier in pricing.by_tier


# ---- local-GPU provider (KL1) -----------------------------------------


def test_is_local_model_recognizes_provider_prefixes() -> None:
    assert cc.is_local_model("ollama/qwen3:30b") is True
    assert cc.is_local_model("local/foo") is True
    assert cc.is_local_model("vllm/qwen3") is True
    assert cc.is_local_model("claude-opus-4-7") is False
    assert cc.is_local_model("claude-haiku-4-5-20251001") is False


def test_model_to_tier_maps_local_prefix_to_local() -> None:
    assert cc._model_to_tier("ollama/qwen3:30b") == "local"
    # A local prefix wins over a Claude tier substring in the tag.
    assert cc._model_to_tier("ollama/haiku-finetune") == "local"
    # Hosted ids still resolve normally.
    assert cc._model_to_tier("claude-sonnet-4-6") == "sonnet"


def test_embedded_pricing_has_zero_local_tier() -> None:
    # The local tier is embedded (not read from the model-keyed YAML),
    # so it is always present and always free.
    pricing = cc.load_tier_pricing()
    assert pricing.by_tier["local"] == (0.0, 0.0)
    assert pricing.rate_for_tier("local") == (0.0, 0.0)


def test_load_tier_map_provider_builds_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # provider= must select the tier_map_<provider>.yaml filename.
    captured: dict[str, str] = {}

    def fake_candidates(env_var: str, filename: str, explicit: Path | None):
        captured["filename"] = filename
        return [tmp_path / "nope.yaml"]  # force embedded fallback.

    monkeypatch.setattr(cc, "_candidate_paths", fake_candidates)
    cc.load_tier_map(provider="local")
    assert captured["filename"] == "tier_map_local.yaml"


def test_load_tier_map_default_provider_is_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Backward compatibility: no provider arg still resolves claude.
    captured: dict[str, str] = {}

    def fake_candidates(env_var: str, filename: str, explicit: Path | None):
        captured["filename"] = filename
        return [tmp_path / "nope.yaml"]

    monkeypatch.setattr(cc, "_candidate_paths", fake_candidates)
    cc.load_tier_map()
    assert captured["filename"] == "tier_map_claude.yaml"


def test_load_tier_map_local_provider_finds_shipped_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The engine ships tier_map_local.yaml at the repo root; an ancestor
    # walk from this package finds it, and every tier is a local model.
    monkeypatch.delenv(cc.ENV_TIER_MAP_PATH, raising=False)
    tier_map = cc.load_tier_map(provider="local")
    assert tier_map.source.endswith("tier_map_local.yaml")
    for points in range(1, 7):
        model = tier_map.model_for(points)
        assert cc.is_local_model(model), f"tier {points} is not local: {model}"
        assert tier_map.tier_name_for(points) == "local"
