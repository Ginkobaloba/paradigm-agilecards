"""Anthropic tool-use turn -- translation + parsing, token-free.

The fake client mirrors the SDK's object-style content blocks, the same
shape SdkInvoker's own tests use.
"""
from __future__ import annotations

from typing import Any

from cards_runner.providers.anthropic_adapter import AnthropicAdapter
from cards_runner.providers.base import (
    AssistantTurn,
    ToolCall,
    ToolResultMsg,
    ToolResults,
    ToolSpec,
    ToolTurnRequest,
    UserText,
)


class _Usage:
    def __init__(self, i: int, o: int) -> None:
        self.input_tokens = i
        self.output_tokens = o


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _ToolUseBlock:
    def __init__(self, id: str, name: str, input: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input


class _Message:
    def __init__(self, blocks: list[Any], stop_reason: str = "tool_use") -> None:
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = _Usage(9, 4)


class _Messages:
    def __init__(self, message: _Message) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return self._message


class _Client:
    def __init__(self, message: _Message) -> None:
        self.messages = _Messages(message)


_READ = ToolSpec(
    name="file_read",
    description="read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)


def test_anthropic_tool_turn_parses_tool_use_blocks() -> None:
    client = _Client(
        _Message([_ToolUseBlock("tu_1", "file_read", {"path": "a.py"})])
    )
    adapter = AnthropicAdapter(client=client)
    result = adapter.tool_turn(
        ToolTurnRequest(
            model="claude-haiku-4-5-20251001",
            system="sys",
            messages=(UserText("go"),),
            tools=(_READ,),
            max_output_tokens=256,
        )
    )
    assert result.finished is False
    assert result.tool_calls == (
        ToolCall(id="tu_1", name="file_read", arguments={"path": "a.py"}),
    )
    assert result.input_tokens == 9
    assert result.output_tokens == 4

    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5-20251001"
    assert call["system"] == "sys"
    assert call["tools"][0] == {
        "name": "file_read",
        "description": "read a file",
        "input_schema": _READ.parameters,
    }
    assert call["messages"][0] == {"role": "user", "content": "go"}


def test_anthropic_tool_turn_finished_when_text_only() -> None:
    client = _Client(_Message([_TextBlock("all done")], stop_reason="end_turn"))
    adapter = AnthropicAdapter(client=client)
    result = adapter.tool_turn(
        ToolTurnRequest(
            model="claude-x",
            system="s",
            messages=(UserText("go"),),
            tools=(),
            max_output_tokens=10,
        )
    )
    assert result.finished is True
    assert result.tool_calls == ()
    assert result.text == "all done"


def test_anthropic_tool_turn_echoes_assistant_and_tool_results() -> None:
    client = _Client(_Message([_TextBlock("done")], stop_reason="end_turn"))
    adapter = AnthropicAdapter(client=client)
    adapter.tool_turn(
        ToolTurnRequest(
            model="claude-x",
            system="s",
            messages=(
                UserText("go"),
                AssistantTurn(
                    text="reading",
                    tool_calls=(
                        ToolCall(id="c1", name="file_read", arguments={"path": "a"}),
                    ),
                ),
                ToolResults(
                    results=(
                        ToolResultMsg(
                            tool_call_id="c1",
                            name="file_read",
                            content="contents",
                            is_error=False,
                        ),
                    )
                ),
            ),
            tools=(),
            max_output_tokens=10,
        )
    )
    msgs = client.messages.calls[0]["messages"]
    assert msgs[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "reading"},
            {"type": "tool_use", "id": "c1", "name": "file_read", "input": {"path": "a"}},
        ],
    }
    assert msgs[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "c1",
                "is_error": False,
                "content": "contents",
            }
        ],
    }
