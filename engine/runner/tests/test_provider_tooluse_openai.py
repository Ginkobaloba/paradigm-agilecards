"""OpenAI-compatible tool-use turn -- translation + parsing, network-free.

This is the local tool-calling path (Ollama / vLLM / OpenAI all share
the OpenAI function-calling schema). The adapter translates the neutral
conversation to OpenAI messages+tools and parses tool_calls back out.
"""
from __future__ import annotations

import json
from typing import Any

from cards_runner.providers.base import (
    AssistantTurn,
    ToolCall,
    ToolResultMsg,
    ToolResults,
    ToolSpec,
    ToolTurnRequest,
    UserText,
)
from cards_runner.providers.openai_compat import OpenAICompatAdapter


class _FakePost:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, *, headers: dict[str, str], body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.response


_READ = ToolSpec(
    name="file_read",
    description="read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)


def test_tool_turn_parses_tool_calls() -> None:
    fake = _FakePost(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "file_read",
                                    "arguments": '{"path": "a.py"}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3},
        }
    )
    adapter = OpenAICompatAdapter(
        base_url="http://localhost:11434/v1", api_key=None, post_json=fake
    )
    result = adapter.tool_turn(
        ToolTurnRequest(
            model="qwen3:30b",
            system="sys",
            messages=(UserText("do the card"),),
            tools=(_READ,),
            max_output_tokens=256,
        )
    )
    assert result.finished is False
    assert result.tool_calls == (
        ToolCall(id="call_1", name="file_read", arguments={"path": "a.py"}),
    )
    assert result.input_tokens == 8
    assert result.output_tokens == 3

    body = fake.calls[0]["body"]
    assert body["tools"][0] == {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "read a file",
            "parameters": _READ.parameters,
        },
    }
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "do the card"}


def test_tool_turn_finished_when_no_tool_calls() -> None:
    fake = _FakePost({"choices": [{"message": {"content": "all done"}}], "usage": {}})
    adapter = OpenAICompatAdapter(base_url="http://x/v1", api_key=None, post_json=fake)
    result = adapter.tool_turn(
        ToolTurnRequest(
            model="m",
            system="s",
            messages=(UserText("go"),),
            tools=(),
            max_output_tokens=10,
        )
    )
    assert result.finished is True
    assert result.tool_calls == ()
    assert result.text == "all done"


def test_tool_turn_echoes_assistant_and_tool_results() -> None:
    fake = _FakePost({"choices": [{"message": {"content": "done"}}], "usage": {}})
    adapter = OpenAICompatAdapter(base_url="http://x/v1", api_key=None, post_json=fake)
    adapter.tool_turn(
        ToolTurnRequest(
            model="m",
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
    msgs = fake.calls[0]["body"]["messages"]
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["tool_calls"][0]["id"] == "c1"
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "file_read"
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"path": "a"}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "contents"}


def test_tool_turn_tolerates_malformed_tool_arguments() -> None:
    # A weak local model can emit invalid JSON in `arguments`. That must
    # degrade to empty args, not crash the worker.
    fake = _FakePost(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c9",
                                "type": "function",
                                "function": {"name": "shell", "arguments": "{not json"},
                            }
                        ],
                    }
                }
            ],
            "usage": {},
        }
    )
    adapter = OpenAICompatAdapter(base_url="http://x/v1", api_key=None, post_json=fake)
    result = adapter.tool_turn(
        ToolTurnRequest(
            model="m", system="s", messages=(UserText("go"),), tools=(), max_output_tokens=10
        )
    )
    assert result.tool_calls == (ToolCall(id="c9", name="shell", arguments={}),)
