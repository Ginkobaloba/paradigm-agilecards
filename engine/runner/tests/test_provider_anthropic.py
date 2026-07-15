"""Anthropic adapter -- token-free unit tests via a fake client.

The fake mirrors the shape SdkInvoker's own tests use: a client whose
`.messages.create(...)` returns an object with text content blocks and a
usage object.
"""
from __future__ import annotations

from typing import Any

from cards_runner.providers import CompletionRequest
from cards_runner.providers.anthropic_adapter import AnthropicAdapter


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Message:
    def __init__(self, text: str, usage: _Usage) -> None:
        self.content = [_Block(text)]
        self.usage = usage


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


def test_anthropic_adapter_completes_and_reports_usage() -> None:
    client = _Client(_Message("hi there", _Usage(20, 8)))
    adapter = AnthropicAdapter(client=client)
    result = adapter.complete(
        CompletionRequest(
            model="claude-haiku-4-5-20251001",
            system="s",
            user="u",
            max_output_tokens=128,
        )
    )
    assert result.text == "hi there"
    assert result.input_tokens == 20
    assert result.output_tokens == 8

    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5-20251001"
    assert call["max_tokens"] == 128
    assert call["system"] == "s"
    assert call["messages"] == [{"role": "user", "content": "u"}]


def test_anthropic_adapter_tolerates_missing_usage() -> None:
    class _NoUsage:
        content = [_Block("x")]

    client = _Client(_NoUsage())  # type: ignore[arg-type]
    adapter = AnthropicAdapter(client=client)
    result = adapter.complete(
        CompletionRequest(model="claude-x", system="s", user="u", max_output_tokens=8)
    )
    assert result.text == "x"
    assert result.input_tokens == 0
    assert result.output_tokens == 0
