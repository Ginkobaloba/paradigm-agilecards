"""Cost metering and cost-cap enforcement -- pure, token-free unit tests.

Nothing here touches the network or the SDK. The governor is the
layer that makes RUNNER_CONTRACT.md's "Cost cap enforcement" real, so
it gets exercised hard in isolation before the SDK is anywhere near.
"""
from __future__ import annotations

import pytest

from cards_runner.worker_stub.cost import (
    CostCapExceeded,
    CostGovernor,
    CostMeter,
    Pricing,
    model_tier,
)


def test_model_tier_substring_match() -> None:
    assert model_tier("claude-haiku-4-5-20251001") == "haiku"
    assert model_tier("claude-sonnet-4-6") == "sonnet"
    assert model_tier("claude-opus-4-6") == "opus"


def test_model_tier_unknown_is_priced_as_opus() -> None:
    # Unknown models must price high so the cap errs toward halting.
    assert model_tier("some-future-model") == "opus"


def test_pricing_call_cost_math() -> None:
    pricing = Pricing(table={"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0),
                             "opus": (15.0, 75.0)})
    # 1M input tokens at $1/Mtok == $1; 1M output at $5/Mtok == $5.
    assert pricing.call_cost("claude-haiku-x", 1_000_000, 0) == pytest.approx(1.0)
    assert pricing.call_cost("claude-haiku-x", 0, 1_000_000) == pytest.approx(5.0)
    assert pricing.call_cost("claude-opus-x", 1_000_000, 1_000_000) == pytest.approx(90.0)


def test_pricing_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARDS_RUNNER_PRICING_JSON", '{"haiku": [2.0, 9.0]}')
    pricing = Pricing.default()
    assert pricing.rates("claude-haiku-x") == (2.0, 9.0)
    # Untouched tiers keep their defaults.
    assert pricing.rates("claude-opus-x") == (15.0, 75.0)


def test_pricing_env_override_malformed_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARDS_RUNNER_PRICING_JSON", "not json at all")
    pricing = Pricing.default()  # must not raise.
    assert pricing.rates("claude-haiku-x") == (1.0, 5.0)


def test_meter_accumulates() -> None:
    meter = CostMeter(pricing=Pricing.default())
    meter.record("claude-haiku-x", 1000, 2000)
    meter.record("claude-haiku-x", 500, 100)
    assert meter.input_tokens == 1500
    assert meter.output_tokens == 2100
    assert meter.total_tokens == 3600
    assert meter.calls == 2
    assert meter.usd > 0
    snap = meter.snapshot()
    assert snap["total_tokens"] == 3600
    assert snap["by_model"]["claude-haiku-x"]["calls"] == 2


def test_governor_with_no_cap_never_raises() -> None:
    gov = CostGovernor.create(None)
    gov.before_call("claude-opus-x", est_input_tokens=10_000_000,
                    max_output_tokens=10_000_000)
    gov.record_call("claude-opus-x", 10_000_000, 10_000_000)
    gov.before_tool("anything")  # no raise.
    assert gov.meter.usd > 0


def test_before_call_raises_when_already_over_budget() -> None:
    gov = CostGovernor.create(0.01)
    gov.meter.record("claude-opus-x", 1_000_000, 0)  # $15, already over.
    with pytest.raises(CostCapExceeded) as exc:
        gov.before_call("claude-haiku-x", est_input_tokens=1,
                        max_output_tokens=1)
    assert exc.value.stage == "pre_call"


def test_before_call_raises_on_projection_overrun() -> None:
    gov = CostGovernor.create(0.05)
    # Nothing spent yet, but a worst-case opus call blows the cap.
    with pytest.raises(CostCapExceeded) as exc:
        gov.before_call("claude-opus-x", est_input_tokens=1_000_000,
                        max_output_tokens=1_000_000)
    assert exc.value.stage == "pre_call"
    assert exc.value.projected_usd is not None


def test_before_call_passes_when_call_fits() -> None:
    gov = CostGovernor.create(100.0)
    gov.before_call("claude-haiku-x", est_input_tokens=2000,
                    max_output_tokens=2048)  # ~$0.01, well under.


def test_record_call_raises_when_call_breaches_cap() -> None:
    gov = CostGovernor.create(0.001)
    with pytest.raises(CostCapExceeded) as exc:
        gov.record_call("claude-haiku-x", 1_000_000, 0)  # $1 >> $0.001.
    assert exc.value.stage == "post_call"
    # The usage is still recorded on the meter before the raise.
    assert gov.meter.input_tokens == 1_000_000


def test_before_tool_raises_only_when_over() -> None:
    gov = CostGovernor.create(0.50)
    gov.before_tool("read_file")  # under budget, fine.
    gov.meter.record("claude-opus-x", 1_000_000, 0)  # $15, over.
    with pytest.raises(CostCapExceeded) as exc:
        gov.before_tool("read_file")
    assert exc.value.stage == "pre_tool"


# ---- local-GPU provider (KL1) ----------------------------------------


def test_local_model_recognized_not_priced_as_opus() -> None:
    # A provider-prefixed local model must NOT fall through to the
    # opus bucket (the pre-KL1 behavior that tripped caps immediately).
    assert model_tier("ollama/qwen3:30b") == "local"
    assert model_tier("local/whatever") == "local"
    assert model_tier("vllm/qwen3-coder") == "local"
    # A local prefix wins even when the tag contains a Claude tier word.
    assert model_tier("ollama/haiku-finetune") == "local"
    # Hosted Claude ids are unaffected.
    assert model_tier("claude-opus-4-7") == "opus"
    assert model_tier("claude-haiku-4-5-20251001") == "haiku"


def test_local_model_prices_at_zero() -> None:
    pricing = Pricing.default()
    assert pricing.rates("ollama/qwen3:30b") == (0.0, 0.0)
    # Millions of tokens, still $0 -- local inference is free.
    assert pricing.call_cost("ollama/qwen3:30b", 5_000_000, 5_000_000) == 0.0


def test_local_run_never_trips_cost_cap() -> None:
    # The whole point of the GPU fallback: a positive cap plus huge token
    # counts must never halt a local run, because cost stays $0.
    gov = CostGovernor.create(0.01)
    gov.before_call("ollama/qwen3:30b", est_input_tokens=10_000_000,
                    max_output_tokens=10_000_000)
    gov.record_call("ollama/qwen3:30b", 10_000_000, 10_000_000)
    gov.before_tool("edit_file")
    assert gov.meter.usd == 0.0


def test_bare_local_floor_token_prices_as_local() -> None:
    # The `local` tier sentinel (used as a model_floor) must resolve to
    # the local tier, not opus -- otherwise it clamps local cards upward.
    assert model_tier("local") == "local"
