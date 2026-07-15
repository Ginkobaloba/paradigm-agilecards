"""SdkInvoker -- exercised against a fake Anthropic client.

Every test here injects a fake client, so the whole file runs at
**zero token cost**. The one real end-to-end SDK call lives in the
2b-ii verification run documented in the handoff, not in the suite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cards_runner.common.types import CardSnapshot
from cards_runner.worker_stub.invoker import InvokeRequest
from cards_runner.worker_stub.sdk_invoker import SdkInvoker


# --- fake Anthropic client ------------------------------------------


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(
        self, text: str, *, input_tokens: int = 800, output_tokens: int = 200
    ) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, responses: list[Any], *, raises: Exception | None = None):
        self._responses = list(responses)
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        if not self._responses:
            raise AssertionError("SdkInvoker made an unexpected extra call")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[Any], *, raises: Exception | None = None):
        self.messages = _FakeMessages(responses, raises=raises)


def _request(
    *,
    points: int = 2,
    cost_cap: float | None = None,
    model_floor: str = "haiku",
    body: str = "Add a one-line docstring to foo().",
) -> InvokeRequest:
    fm: dict[str, Any] = {
        "points": points,
        "cost_cap_usd": cost_cap,
        "model_floor": model_floor,
        "model": "claude-haiku-4-5-20251001",
        "title": "trivial test card",
    }
    snap = CardSnapshot(card_id="bTST-99-sdk", frontmatter=fm, body=body)
    return InvokeRequest(
        snapshot=snap,
        worktree=Path("/tmp/wt"),
        attempt_trace_id="attempt-xyz",
        trace_id="trace-xyz",
    )


# --- tests -----------------------------------------------------------


def test_high_confidence_single_turn_settles() -> None:
    client = _FakeClient([_FakeMessage("Done.\nCONFIDENCE: 0.95")])
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request())
    assert result.success is True
    assert result.halt_kind is None
    assert result.cascade_history == ()
    assert result.actual_tokens == 1000  # 800 in + 200 out.
    assert result.actual_cost_usd > 0
    assert result.model_used == "claude-haiku-4-5-20251001"
    assert len(client.messages.calls) == 1


def test_low_confidence_climbs_then_exhausts() -> None:
    client = _FakeClient([
        _FakeMessage("partial\nCONFIDENCE: 0.30"),
        _FakeMessage("still rough\nCONFIDENCE: 0.30"),
        _FakeMessage("no better\nCONFIDENCE: 0.30"),
    ])
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request(points=2))
    assert len(client.messages.calls) == 3            # 1 + 2 escalations.
    assert len(result.cascade_history) == 2
    assert result.halt_kind == "cascade_exhausted"
    assert result.success is False
    first = result.cascade_history[0]
    assert first["from_tier"] == 2 and first["to_tier"] == 3
    assert first["from_model"] == "claude-haiku-4-5-20251001"
    assert first["to_model"] == "claude-sonnet-4-6"
    assert first["attempt_trace_id"] == "attempt-xyz"
    assert first["confidence_at_escalation"] == pytest.approx(0.30)


def test_cascade_settles_after_one_escalation() -> None:
    client = _FakeClient([
        _FakeMessage("first pass weak\nCONFIDENCE: 0.40"),
        _FakeMessage("sonnet nails it\nCONFIDENCE: 0.92"),
    ])
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request(points=2))
    assert len(client.messages.calls) == 2
    assert len(result.cascade_history) == 1
    assert result.halt_kind is None
    assert result.success is True
    assert result.model_used == "claude-sonnet-4-6"


def test_cost_cap_halts_before_the_first_call() -> None:
    # A cap this small cannot fit even one worst-case haiku call.
    client = _FakeClient([_FakeMessage("unreached\nCONFIDENCE: 0.99")])
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request(cost_cap=0.0001))
    assert result.halt_kind == "cost_cap"
    assert result.success is False
    assert result.actual_tokens == 0
    assert len(client.messages.calls) == 0


def test_cost_cap_halts_after_a_call_overruns() -> None:
    # The pre-call projection fits, but the call's real usage (a huge
    # input count) breaches the cap -- the post-call hook catches it.
    client = _FakeClient([
        _FakeMessage("big", input_tokens=10_000_000, output_tokens=50)
    ])
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request(cost_cap=0.05))
    assert result.halt_kind == "cost_cap"
    assert result.success is False
    assert len(client.messages.calls) == 1
    assert result.actual_tokens > 10_000_000


def test_no_cost_cap_never_halts_on_spend() -> None:
    client = _FakeClient([
        _FakeMessage(
            "huge but fine\nCONFIDENCE: 0.99",
            input_tokens=9_000_000,
            output_tokens=9_000_000,
        )
    ])
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request(cost_cap=None))
    # A wildly expensive run is not halted when the card set no cap.
    assert result.success is True
    assert result.halt_kind is None
    assert result.actual_cost_usd > 50  # 18M haiku tokens, uncapped.


def test_missing_confidence_marker_does_not_force_escalation() -> None:
    client = _FakeClient([_FakeMessage("a report with no marker line")])
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request())
    # A missing marker defaults high: settle, do not burn tokens.
    assert len(client.messages.calls) == 1
    assert result.cascade_history == ()
    assert result.success is True


def test_sdk_exception_becomes_a_failed_result() -> None:
    client = _FakeClient([], raises=RuntimeError("network is down"))
    inv = SdkInvoker(client=client, api_key="fake")
    result = inv.invoke(_request())
    assert result.success is False
    assert result.halt_kind is None  # not a halt; an error.
    assert "network is down" in result.completion_notes_markdown


def test_model_floor_clamps_the_starting_tier() -> None:
    client = _FakeClient([_FakeMessage("done\nCONFIDENCE: 0.99")])
    inv = SdkInvoker(client=client, api_key="fake")
    # points=1 would start at haiku, but an opus floor forces the
    # start up to the opus band. The chunk 4 canonical tier_map_claude.yaml
    # pegs the opus tier at the 4.7 model id; chunk 3's embedded stand-in
    # used the older 4.6 id, which the canonical wiring corrected.
    result = inv.invoke(_request(points=1, model_floor="opus"))
    assert client.messages.calls[0]["model"] == "claude-opus-4-7"
    assert result.model_used == "claude-opus-4-7"


def test_local_model_floor_does_not_clamp_upward() -> None:
    client = _FakeClient([_FakeMessage("done\nCONFIDENCE: 0.99")])
    inv = SdkInvoker(client=client, api_key="fake")
    # The 'local' floor sentinel must NOT clamp a tier-1 card upward.
    # Pre-fix, model_tier("local") returned "opus" and forced points 5.
    # Post-fix it resolves to the 'local' tier, so the card starts at its
    # planned tier 1 (haiku, since this invoker still uses the claude map).
    result = inv.invoke(_request(points=1, model_floor="local"))
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5-20251001"
    assert result.model_used == "claude-haiku-4-5-20251001"


def test_escalation_cap_is_hard_two() -> None:
    inv = SdkInvoker(client=_FakeClient([]), api_key="fake", max_escalations=9)
    assert inv.max_escalations == 2  # clamped to the contract cap.
