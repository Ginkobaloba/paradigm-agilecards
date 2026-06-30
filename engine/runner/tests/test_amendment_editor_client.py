"""Tests for `cards_runner.daemon.amendment_editor_client`."""
from __future__ import annotations

from typing import Any

import pytest

from cards_runner.common.project_config import ReviewerConfig
from cards_runner.daemon.ac_editor import AmendmentEdit
from cards_runner.daemon.amendment_editor_client import (
    AnthropicAmendmentEditorClient,
    StaticAmendmentEditorClient,
    _EDIT_TOOL_NAME,
    _EDIT_TOOL_SCHEMA,
    _payload_to_edit,
)


# ---- static client --------------------------------------------------


def test_static_client_returns_default_when_card_unspecified() -> None:
    default = AmendmentEdit(
        ac_index=0, description="x", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    client = StaticAmendmentEditorClient(default=default)
    out = client.edit(
        card_id="bUnknown", card_body="body", change_request="cr",
        ac_items=[{"description": "x"}],
        reviewer=ReviewerConfig(),
    )
    assert out is default
    assert client.calls[0]["card_id"] == "bUnknown"


def test_static_client_per_card_override_wins() -> None:
    default = AmendmentEdit(
        ac_index=0, description="default", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    override = AmendmentEdit(
        ac_index=1, description="override", check_type="command",
        amendment_reason="y", confidence=0.95,
    )
    client = StaticAmendmentEditorClient(
        default=default, edits_by_card={"bSPECIAL": override},
    )
    assert client.edit(
        card_id="bSPECIAL", card_body="b", change_request="cr",
        ac_items=[], reviewer=ReviewerConfig(),
    ) is override
    assert client.edit(
        card_id="bOther", card_body="b", change_request="cr",
        ac_items=[], reviewer=ReviewerConfig(),
    ) is default


def test_static_client_records_explicit_none_per_card() -> None:
    """A per-card None means 'editor declines' for that card even if
    default would have returned an edit."""
    default = AmendmentEdit(
        ac_index=0, description="d", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    client = StaticAmendmentEditorClient(
        default=default, edits_by_card={"bSKIP": None},
    )
    assert client.edit(
        card_id="bSKIP", card_body="b", change_request="cr",
        ac_items=[], reviewer=ReviewerConfig(),
    ) is None


# ---- _payload_to_edit coercion --------------------------------------


def test_payload_to_edit_happy_path() -> None:
    payload = {
        "ac_index": 1,
        "description": "amended desc",
        "check_type": "command",
        "check_fields": {"command": ["echo", "hi"]},
        "amendment_reason": "needed update",
        "confidence": 0.92,
    }
    edit = _payload_to_edit(payload, model_id="claude-sonnet-4-6")
    assert edit is not None
    assert edit.ac_index == 1
    assert edit.description == "amended desc"
    assert edit.check_type == "command"
    assert edit.check_fields == {"command": ["echo", "hi"]}
    assert edit.amendment_reason == "needed update"
    assert edit.confidence == pytest.approx(0.92)
    assert edit.model_used == "claude-sonnet-4-6"


def test_payload_to_edit_clamps_confidence() -> None:
    edit = _payload_to_edit(
        {
            "ac_index": 0, "description": "d", "check_type": "t",
            "amendment_reason": "r", "confidence": 1.5,
        },
        model_id="m",
    )
    assert edit is not None and edit.confidence == 1.0
    edit2 = _payload_to_edit(
        {
            "ac_index": 0, "description": "d", "check_type": "t",
            "amendment_reason": "r", "confidence": -0.5,
        },
        model_id="m",
    )
    assert edit2 is not None and edit2.confidence == 0.0


def test_payload_to_edit_rejects_missing_required() -> None:
    assert _payload_to_edit(
        {"ac_index": 0, "description": "d", "check_type": "t"}, model_id="m"
    ) is None  # missing amendment_reason + confidence


def test_payload_to_edit_rejects_non_int_index() -> None:
    edit = _payload_to_edit(
        {
            "ac_index": "zero", "description": "d", "check_type": "t",
            "amendment_reason": "r", "confidence": 0.9,
        },
        model_id="m",
    )
    assert edit is None


def test_payload_to_edit_rejects_empty_description() -> None:
    edit = _payload_to_edit(
        {
            "ac_index": 0, "description": "   ", "check_type": "t",
            "amendment_reason": "r", "confidence": 0.9,
        },
        model_id="m",
    )
    assert edit is None


def test_payload_to_edit_rejects_non_dict_check_fields() -> None:
    edit = _payload_to_edit(
        {
            "ac_index": 0, "description": "d", "check_type": "t",
            "amendment_reason": "r", "confidence": 0.9,
            "check_fields": ["not", "a", "dict"],
        },
        model_id="m",
    )
    assert edit is None


def test_payload_to_edit_missing_check_fields_defaults_to_empty() -> None:
    edit = _payload_to_edit(
        {
            "ac_index": 0, "description": "d", "check_type": "t",
            "amendment_reason": "r", "confidence": 0.9,
        },
        model_id="m",
    )
    assert edit is not None
    assert edit.check_fields == {}


# ---- AnthropicAmendmentEditorClient ---------------------------------


class _FakeBlock:
    """Quack like a tool_use content block from the Anthropic SDK."""

    def __init__(self, btype: str, name: str | None = None,
                 inp: Any | None = None, text: str | None = None) -> None:
        self.type = btype
        self.name = name
        self.input = inp
        self.text = text


class _FakeResponse:
    def __init__(self, blocks: list[_FakeBlock]) -> None:
        self.content = blocks


class _FakeMessages:
    def __init__(self, response: Any | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response: Any | Exception) -> None:
        self.messages = _FakeMessages(response)


def test_anthropic_client_returns_edit_on_clean_tool_use() -> None:
    block = _FakeBlock(
        btype="tool_use",
        name=_EDIT_TOOL_NAME,
        inp={
            "ac_index": 0,
            "description": "amended",
            "check_type": "file_exists",
            "check_fields": {"path": "x.md"},
            "amendment_reason": "renamed",
            "confidence": 0.95,
        },
    )
    fake = _FakeAnthropicClient(_FakeResponse([block]))
    client = AnthropicAmendmentEditorClient(client=fake)
    edit = client.edit(
        card_id="bX", card_body="body", change_request="cr",
        ac_items=[{"description": "x", "type": "file_exists"}],
        reviewer=ReviewerConfig(enabled=True, model_id="claude-sonnet-4-6"),
    )
    assert edit is not None
    assert edit.description == "amended"
    assert edit.model_used == "claude-sonnet-4-6"
    # The SDK call was constrained to the editor tool.
    call = fake.messages.calls[0]
    assert call["model"] == "claude-sonnet-4-6"
    assert call["tool_choice"]["name"] == _EDIT_TOOL_NAME
    assert call["tools"][0]["name"] == _EDIT_TOOL_NAME
    assert call["tools"][0]["input_schema"] == _EDIT_TOOL_SCHEMA


def test_anthropic_client_returns_none_when_no_tool_use() -> None:
    # A free-text response despite tool_choice; treat as refusal.
    block = _FakeBlock(btype="text", text="I don't think any edit is right.")
    fake = _FakeAnthropicClient(_FakeResponse([block]))
    client = AnthropicAmendmentEditorClient(client=fake)
    assert client.edit(
        card_id="bX", card_body="b", change_request="cr",
        ac_items=[{}], reviewer=ReviewerConfig(),
    ) is None


def test_anthropic_client_returns_none_when_tool_name_mismatch() -> None:
    block = _FakeBlock(
        btype="tool_use",
        name="some_other_tool",
        inp={"ac_index": 0, "description": "x", "check_type": "t",
             "amendment_reason": "r", "confidence": 0.9},
    )
    fake = _FakeAnthropicClient(_FakeResponse([block]))
    client = AnthropicAmendmentEditorClient(client=fake)
    assert client.edit(
        card_id="bX", card_body="b", change_request="cr",
        ac_items=[{}], reviewer=ReviewerConfig(),
    ) is None


def test_anthropic_client_returns_none_on_sdk_exception() -> None:
    fake = _FakeAnthropicClient(RuntimeError("network down"))
    client = AnthropicAmendmentEditorClient(client=fake)
    assert client.edit(
        card_id="bX", card_body="b", change_request="cr",
        ac_items=[{}], reviewer=ReviewerConfig(),
    ) is None


def test_anthropic_client_truncates_huge_user_prompt() -> None:
    """A 100KB body should not blow up the call. We can't directly
    observe the user_prompt that goes onto the messages list, but we
    can confirm the call still completes and the result is returned."""
    block = _FakeBlock(
        btype="tool_use", name=_EDIT_TOOL_NAME,
        inp={"ac_index": 0, "description": "ok", "check_type": "file_exists",
             "amendment_reason": "ok", "confidence": 0.95},
    )
    fake = _FakeAnthropicClient(_FakeResponse([block]))
    client = AnthropicAmendmentEditorClient(client=fake)
    huge_body = "x" * 100_000
    edit = client.edit(
        card_id="bX", card_body=huge_body, change_request="cr",
        ac_items=[{"description": "x", "type": "file_exists"}],
        reviewer=ReviewerConfig(),
    )
    assert edit is not None
    user_msg = fake.messages.calls[0]["messages"][0]["content"]
    assert len(user_msg) <= 64000 + 100  # within the truncation guard.


def test_anthropic_client_includes_prompt_extra_in_system_prompt() -> None:
    block = _FakeBlock(
        btype="tool_use", name=_EDIT_TOOL_NAME,
        inp={"ac_index": 0, "description": "ok", "check_type": "file_exists",
             "amendment_reason": "ok", "confidence": 0.95},
    )
    fake = _FakeAnthropicClient(_FakeResponse([block]))
    client = AnthropicAmendmentEditorClient(client=fake)
    client.edit(
        card_id="bX", card_body="b", change_request="cr",
        ac_items=[{"description": "x"}],
        reviewer=ReviewerConfig(prompt_extra="PROJECT STYLE: use snake_case."),
    )
    sys = fake.messages.calls[0]["system"]
    assert "PROJECT STYLE" in sys
