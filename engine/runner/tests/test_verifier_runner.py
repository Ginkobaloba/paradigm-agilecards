"""Verifier orchestrator -- end-to-end with deterministic items
and a fake subjective evaluator client.

Token-free. The subjective evaluator is exercised against the same
fake-client shape `test_sdk_invoker.py` uses, so this whole file runs
without any real Anthropic call.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cards_runner.verifier import verify_card
from cards_runner.verifier.runner import (
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_STANDUP,
)


# --- fake Anthropic client (mirrors test_sdk_invoker.py shape) ------


class _FakeUsage:
    def __init__(self, in_t: int = 100, out_t: int = 30) -> None:
        self.input_tokens = in_t
        self.output_tokens = out_t


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage()
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("verifier fake out of responses")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _FakeMessages(responses)


def _eval_response(verdicts: list[dict[str, Any]]) -> _FakeMessage:
    """Build a fake evaluator response: a JSON-only assistant turn."""
    return _FakeMessage(json.dumps({"items": verdicts}))


# --- helpers ------------------------------------------------------


def _body_with(*ac_blocks: str) -> str:
    return (
        "## Acceptance criteria\n\n"
        "```yaml\n"
        "acceptance_criteria:\n"
        + "".join(ac_blocks)
        + "```\n"
    )


# --- pure-deterministic flow --------------------------------------


def test_all_deterministic_pass_yields_overall_pass(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    body = _body_with(
        "  - description: 'readme present'\n"
        "    type: file_exists\n"
        "    path: README.md\n"
    )
    result = verify_card(
        card_id="bTST-90-x",
        card_body=body,
        worktree=tmp_path,
    )
    assert result.overall_status == VERDICT_PASS
    assert len(result.items) == 1
    assert result.items[0].handler_result.passed is True
    assert result.standup_reason_items == ()


def test_failing_deterministic_yields_overall_fail(tmp_path: Path) -> None:
    body = _body_with(
        "  - description: 'readme present'\n"
        "    type: file_exists\n"
        "    path: README.md\n"
    )
    result = verify_card(
        card_id="bTST-90-y",
        card_body=body,
        worktree=tmp_path,
    )
    assert result.overall_status == VERDICT_FAIL
    assert result.items[0].handler_result.passed is False


def test_no_ac_items_is_pass(tmp_path: Path) -> None:
    # Card body with no acceptance_criteria block -> verifier passes.
    result = verify_card(
        card_id="bTST-90-empty",
        card_body="## Context\n\nplain card.",
        worktree=tmp_path,
    )
    assert result.overall_status == VERDICT_PASS


def test_schema_error_routes_to_fail(tmp_path: Path) -> None:
    body = _body_with(
        "  - description: 'bogus'\n"
        "    type: teleport\n"
    )
    result = verify_card(
        card_id="bTST-91-x",
        card_body=body,
        worktree=tmp_path,
    )
    assert result.overall_status == VERDICT_FAIL
    assert "schema" in result.notes.lower()


# --- subjective phase ---------------------------------------------


def test_subjective_pass_at_haiku_settles(tmp_path: Path) -> None:
    body = _body_with(
        "  - description: 'tone is on-brand'\n"
        "    type: subjective\n"
        "    subjective_evidence: 'see commit msg'\n"
    )
    client = _FakeClient([
        _eval_response([
            {"index": 0, "result": "pass", "confidence": 0.95,
             "reasoning": "clear and direct"}
        ])
    ])
    result = verify_card(
        card_id="bTST-92-x",
        card_body=body,
        worktree=tmp_path,
        subjective_client=client,
    )
    assert result.overall_status == VERDICT_PASS
    assert len(client.messages.calls) == 1  # settled at haiku.
    assert len(result.cascade_history_appendix) == 1
    assert result.cascade_history_appendix[0]["tier_attempted"] == "haiku"


def test_subjective_low_confidence_climbs_then_settles(tmp_path: Path) -> None:
    body = _body_with(
        "  - description: 'voice OK'\n"
        "    type: subjective\n"
    )
    client = _FakeClient([
        _eval_response([
            {"index": 0, "result": "pass", "confidence": 0.5, "reasoning": "iffy"}
        ]),
        _eval_response([
            {"index": 0, "result": "pass", "confidence": 0.95, "reasoning": "ok"}
        ]),
    ])
    result = verify_card(
        card_id="bTST-92-y",
        card_body=body,
        worktree=tmp_path,
        subjective_client=client,
    )
    assert result.overall_status == VERDICT_PASS
    assert len(client.messages.calls) == 2  # haiku, then sonnet.
    tiers = [e["tier_attempted"] for e in result.cascade_history_appendix]
    assert tiers == ["haiku", "sonnet"]


def test_subjective_cascade_exhausts_to_standup(tmp_path: Path) -> None:
    body = _body_with(
        "  - description: 'voice OK'\n"
        "    type: subjective\n"
    )
    client = _FakeClient([
        _eval_response([
            {"index": 0, "result": "pass", "confidence": 0.4, "reasoning": "?"}
        ]),
        _eval_response([
            {"index": 0, "result": "pass", "confidence": 0.5, "reasoning": "??"}
        ]),
        _eval_response([
            {"index": 0, "result": "pass", "confidence": 0.6, "reasoning": "???"}
        ]),
    ])
    result = verify_card(
        card_id="bTST-92-z",
        card_body=body,
        worktree=tmp_path,
        subjective_client=client,
    )
    assert result.overall_status == VERDICT_STANDUP
    assert result.standup_reason_items == (0,)
    assert len(client.messages.calls) == 3  # haiku, sonnet, opus.
    assert len(result.standup_reasons) == 1


def test_subjective_disabled_routes_to_standup_with_no_call(
    tmp_path: Path,
) -> None:
    body = _body_with(
        "  - description: 'taste'\n"
        "    type: subjective\n"
    )
    # No client because the cascade is disabled; the verifier should
    # short-circuit to standup review.
    result = verify_card(
        card_id="bTST-93-x",
        card_body=body,
        worktree=tmp_path,
        subjective_disabled=True,
    )
    assert result.overall_status == VERDICT_STANDUP
    assert result.cascade_history_appendix[0]["tier_attempted"] == "disabled"


def test_subjective_with_no_client_routes_to_standup(tmp_path: Path) -> None:
    body = _body_with("  - description: 'taste'\n    type: subjective\n")
    result = verify_card(
        card_id="bTST-93-y",
        card_body=body,
        worktree=tmp_path,
        subjective_client=None,
    )
    assert result.overall_status == VERDICT_STANDUP


def test_mixed_deterministic_fail_and_subjective_pass(tmp_path: Path) -> None:
    # When a deterministic item fails and a subjective item passes,
    # `overall_status` is FAIL (the failed deterministic wins over the
    # passed subjective; standup is reserved for cascade exhaustion).
    body = _body_with(
        "  - description: 'readme exists'\n"
        "    type: file_exists\n"
        "    path: README.md\n"
        "  - description: 'voice'\n"
        "    type: subjective\n"
    )
    client = _FakeClient([
        _eval_response([
            {"index": 1, "result": "pass", "confidence": 0.95, "reasoning": "ok"}
        ])
    ])
    result = verify_card(
        card_id="bTST-94-x",
        card_body=body,
        worktree=tmp_path,
        subjective_client=client,
    )
    assert result.overall_status == VERDICT_FAIL
    # The subjective item still gets its evidence; the deterministic
    # one failed.
    assert any(it.phase == "deterministic" and not it.handler_result.passed
               for it in result.items)


def test_evaluator_returns_no_verdicts_treated_as_low_confidence(
    tmp_path: Path,
) -> None:
    # An evaluator that returns an empty items list at every tier
    # must exhaust the cascade to standup, not silently pass.
    body = _body_with("  - description: 'taste'\n    type: subjective\n")
    client = _FakeClient([
        _eval_response([]),
        _eval_response([]),
        _eval_response([]),
    ])
    result = verify_card(
        card_id="bTST-95-x",
        card_body=body,
        worktree=tmp_path,
        subjective_client=client,
    )
    assert result.overall_status == VERDICT_STANDUP


def test_result_items_in_declaration_order(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
    body = _body_with(
        "  - description: 'license'\n"
        "    type: file_exists\n"
        "    path: LICENSE\n"
        "  - description: 'readme'\n"
        "    type: file_exists\n"
        "    path: README.md\n"
    )
    result = verify_card(
        card_id="bTST-96-x",
        card_body=body,
        worktree=tmp_path,
    )
    assert [it.item_idx for it in result.items] == [0, 1]
