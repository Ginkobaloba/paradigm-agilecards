"""Tests for gate chunk 1: the verifier risk-factor schema + shim.

Two layers:
- `parse_risk_factors` is a total, forgiving parser (unit tests).
- The subjective evaluator surfaces a `risk_factors` list and
  `verify_card` carries it on `VerifierResult`, backward-compatibly
  (a card with no subjective items, or a model that emits none, gets an
  empty tuple). No gate consumes the field yet.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cards_runner.verifier import RiskFactor, parse_risk_factors, verify_card
from cards_runner.verifier.risk_factor import (
    KIND_EXTERNAL_CALL_ADDED,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
)
from cards_runner.verifier.runner import VERDICT_PASS


# ---- parse_risk_factors (pure) ---------------------------------------


def test_parse_valid_entries() -> None:
    raw = [
        {"kind": "external_call_added", "severity": "medium",
         "description": "new fetch() to api.x.com", "location": "a.ts:42",
         "source_item_idx": 1},
        {"kind": "incomplete_test_coverage", "severity": "low",
         "description": "added fn, no test"},
    ]
    rfs = parse_risk_factors(raw)
    assert len(rfs) == 2
    assert rfs[0] == RiskFactor(
        kind=KIND_EXTERNAL_CALL_ADDED, severity=SEVERITY_MEDIUM,
        description="new fetch() to api.x.com", location="a.ts:42",
        source_item_idx=1,
    )
    assert rfs[1].severity == SEVERITY_LOW
    assert rfs[1].location is None
    assert rfs[1].source_item_idx is None


def test_parse_non_list_is_empty() -> None:
    assert parse_risk_factors(None) == ()
    assert parse_risk_factors("nope") == ()
    assert parse_risk_factors({"kind": "x"}) == ()


def test_parse_skips_malformed_entries() -> None:
    raw = [
        "not a dict",
        {"severity": "high", "description": "no kind -> skipped"},
        {"kind": "", "description": "blank kind -> skipped"},
        {"kind": "raw_sql", "severity": "high", "description": "kept"},
    ]
    rfs = parse_risk_factors(raw)
    assert len(rfs) == 1
    assert rfs[0].kind == "raw_sql"
    assert rfs[0].severity == SEVERITY_HIGH


def test_parse_unknown_severity_defaults_low() -> None:
    rfs = parse_risk_factors([{"kind": "guard_removed", "severity": "spicy",
                               "description": "x"}])
    assert rfs[0].severity == SEVERITY_LOW


def test_parse_unknown_kind_preserved_but_flagged() -> None:
    rfs = parse_risk_factors([{"kind": "novel_thing", "severity": "high",
                               "description": "x"}])
    assert rfs[0].kind == "novel_thing"
    assert rfs[0].is_known_kind() is False


def test_parse_bad_source_idx_becomes_none() -> None:
    rfs = parse_risk_factors([{"kind": "raw_sql", "severity": "low",
                               "description": "x", "source_item_idx": "abc"}])
    assert rfs[0].source_item_idx is None


# ---- subjective evaluator + verify_card surfacing --------------------


class _FakeUsage:
    input_tokens = 100
    output_tokens = 30


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
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _FakeMessages(responses)


def _body_with(*ac_blocks: str) -> str:
    return (
        "## Acceptance criteria\n\n"
        "```yaml\n"
        "acceptance_criteria:\n"
        + "".join(ac_blocks)
        + "```\n"
    )


def test_verify_card_surfaces_risk_factors(tmp_path: Path) -> None:
    body = _body_with(
        "  - description: 'tone is on-brand'\n"
        "    type: subjective\n"
    )
    payload = {
        "items": [
            {"index": 0, "result": "pass", "confidence": 0.95,
             "reasoning": "clean"}
        ],
        "risk_factors": [
            {"kind": "external_call_added", "severity": "medium",
             "description": "new outbound call", "location": "x.py:10",
             "source_item_idx": 0}
        ],
    }
    client = _FakeClient([_FakeMessage(json.dumps(payload))])
    result = verify_card(
        card_id="bRF-01", card_body=body, worktree=tmp_path,
        subjective_client=client,
    )
    assert result.overall_status == VERDICT_PASS
    assert len(result.risk_factors) == 1
    rf = result.risk_factors[0]
    assert rf.kind == "external_call_added"
    assert rf.severity == SEVERITY_MEDIUM
    assert rf.location == "x.py:10"


def test_risk_factors_decoupled_from_pass_fail(tmp_path: Path) -> None:
    """A high-severity risk factor must NOT flip the item's pass verdict
    (spec 5.2: honest reporting is cheap only if it can't cost a pass)."""
    body = _body_with(
        "  - description: 'works'\n"
        "    type: subjective\n"
    )
    payload = {
        "items": [{"index": 0, "result": "pass", "confidence": 0.97,
                   "reasoning": "ok"}],
        "risk_factors": [{"kind": "raw_sql", "severity": "high",
                          "description": "string-built query"}],
    }
    client = _FakeClient([_FakeMessage(json.dumps(payload))])
    result = verify_card(
        card_id="bRF-02", card_body=body, worktree=tmp_path,
        subjective_client=client,
    )
    assert result.overall_status == VERDICT_PASS  # still passes
    assert result.risk_factors[0].severity == SEVERITY_HIGH


def test_deterministic_only_card_has_empty_risk_factors(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    body = _body_with(
        "  - description: 'readme present'\n"
        "    type: file_exists\n"
        "    path: README.md\n"
    )
    result = verify_card(card_id="bRF-03", card_body=body, worktree=tmp_path)
    assert result.overall_status == VERDICT_PASS
    assert result.risk_factors == ()


def test_stronger_tier_risk_factors_supersede(tmp_path: Path) -> None:
    """When the cascade climbs, the stronger tier's non-empty risk list
    supersedes the weaker tier's (spec 3.6: strongest read is most
    authoritative)."""
    body = _body_with(
        "  - description: 'tone'\n"
        "    type: subjective\n"
    )
    haiku = {
        "items": [{"index": 0, "result": "pass", "confidence": 0.50,
                   "reasoning": "unsure"}],  # low conf -> climb to sonnet
        "risk_factors": [{"kind": "raw_sql", "severity": "low",
                          "description": "haiku read"}],
    }
    sonnet = {
        "items": [{"index": 0, "result": "pass", "confidence": 0.96,
                   "reasoning": "settled"}],
        "risk_factors": [{"kind": "external_call_added", "severity": "high",
                          "description": "sonnet read"}],
    }
    client = _FakeClient([_FakeMessage(json.dumps(haiku)),
                          _FakeMessage(json.dumps(sonnet))])
    result = verify_card(
        card_id="bRF-05", card_body=body, worktree=tmp_path,
        subjective_client=client,
    )
    assert len(client.messages.calls) == 2  # climbed
    assert len(result.risk_factors) == 1
    assert result.risk_factors[0].kind == "external_call_added"  # sonnet won
    assert result.risk_factors[0].severity == SEVERITY_HIGH


def test_later_empty_risk_list_does_not_clobber(tmp_path: Path) -> None:
    """A stronger tier returning NO risk factors must not erase the
    weaker tier's findings -- the `if tier_risks` guard keeps the last
    non-empty list."""
    body = _body_with(
        "  - description: 'tone'\n"
        "    type: subjective\n"
    )
    haiku = {
        "items": [{"index": 0, "result": "pass", "confidence": 0.50,
                   "reasoning": "unsure"}],
        "risk_factors": [{"kind": "guard_removed", "severity": "medium",
                          "description": "haiku saw it"}],
    }
    sonnet = {
        "items": [{"index": 0, "result": "pass", "confidence": 0.96,
                   "reasoning": "settled"}],
        # no risk_factors key at all
    }
    client = _FakeClient([_FakeMessage(json.dumps(haiku)),
                          _FakeMessage(json.dumps(sonnet))])
    result = verify_card(
        card_id="bRF-06", card_body=body, worktree=tmp_path,
        subjective_client=client,
    )
    assert len(client.messages.calls) == 2
    assert len(result.risk_factors) == 1
    assert result.risk_factors[0].kind == "guard_removed"  # haiku preserved


def test_missing_risk_factors_key_is_empty(tmp_path: Path) -> None:
    """A v1.3 evaluator that predates the field (no risk_factors key)
    yields an empty tuple, not an error."""
    body = _body_with(
        "  - description: 'tone'\n"
        "    type: subjective\n"
    )
    client = _FakeClient([_FakeMessage(json.dumps(
        {"items": [{"index": 0, "result": "pass", "confidence": 0.95,
                    "reasoning": "ok"}]}
    ))])
    result = verify_card(
        card_id="bRF-04", card_body=body, worktree=tmp_path,
        subjective_client=client,
    )
    assert result.risk_factors == ()
