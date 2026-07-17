"""Gemini tool-use turn -- translation + parsing, network-free.

Gemini is the schema outlier: `functionDeclarations` for tools,
`functionCall` parts in the response, `functionResponse` parts for
results. It also carries no tool-call id, so ids are synthesized
deterministically and echoed back by name.
"""
from __future__ import annotations

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
from cards_runner.providers.gemini_adapter import GeminiAdapter


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


def test_gemini_tool_turn_parses_function_calls() -> None:
    fake = _FakePost(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "reading now"},
                            {
                                "functionCall": {
                                    "name": "file_read",
                                    "args": {"path": "a.py"},
                                }
                            },
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
        }
    )
    adapter = GeminiAdapter(api_key="k", post_json=fake)
    result = adapter.tool_turn(
        ToolTurnRequest(
            model="gemini-2.0-flash",
            system="sys",
            messages=(UserText("go"),),
            tools=(_READ,),
            max_output_tokens=256,
        )
    )
    assert result.finished is False
    assert result.text == "reading now"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == "file_read"
    assert call.arguments == {"path": "a.py"}
    assert call.id  # synthesized, non-empty
    assert result.input_tokens == 7
    assert result.output_tokens == 2

    body = fake.calls[0]["body"]
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "file_read",
                    "description": "read a file",
                    "parameters": _READ.parameters,
                }
            ]
        }
    ]
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"][0] == {"role": "user", "parts": [{"text": "go"}]}


def test_gemini_tool_turn_finished_when_text_only() -> None:
    fake = _FakePost(
        {
            "candidates": [{"content": {"parts": [{"text": "all done"}]}}],
            "usageMetadata": {},
        }
    )
    adapter = GeminiAdapter(api_key="k", post_json=fake)
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


def test_gemini_tool_turn_echoes_assistant_and_function_responses() -> None:
    fake = _FakePost(
        {"candidates": [{"content": {"parts": [{"text": "done"}]}}], "usageMetadata": {}}
    )
    adapter = GeminiAdapter(api_key="k", post_json=fake)
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
    contents = fake.calls[0]["body"]["contents"]
    # model turn carries text + functionCall
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0] == {"text": "reading"}
    assert contents[1]["parts"][1] == {
        "functionCall": {"name": "file_read", "args": {"path": "a"}}
    }
    # results come back as a user turn of functionResponse parts, keyed by name
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0] == {
        "functionResponse": {
            "name": "file_read",
            "response": {"content": "contents"},
        }
    }
