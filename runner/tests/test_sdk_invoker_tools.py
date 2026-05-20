"""SdkInvoker with `use_tools=True` -- multi-turn tool-use loop.

Token-free: fake client returns canned message objects with `tool_use`
content blocks; the invoker dispatches them through a `ToolBelt`
rooted at a tmp_path. Mirrors the assertions in `test_sdk_invoker.py`
for the reasoning-only path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cards_runner.common.types import CardSnapshot
from cards_runner.worker_stub.invoker import InvokeRequest
from cards_runner.worker_stub.sdk_invoker import SdkInvoker


# --- fakes -----------------------------------------------------------


class _FakeUsage:
    def __init__(self, input_tokens: int = 100, output_tokens: int = 30) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, *, id: str, name: str, input: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input


class _FakeMessage:
    def __init__(
        self,
        content: list[Any],
        *,
        stop_reason: str = "tool_use",
        input_tokens: int = 100,
        output_tokens: int = 30,
    ) -> None:
        self.content = content
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses: list[_FakeMessage]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("SdkInvoker made an unexpected extra call")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[_FakeMessage]) -> None:
        self.messages = _FakeMessages(responses)


def _request(tmp_path: Path, *, points: int = 2, cost_cap: float | None = None) -> InvokeRequest:
    fm: dict[str, Any] = {
        "points": points,
        "cost_cap_usd": cost_cap,
        "model": "claude-haiku-4-5-20251001",
        "title": "trivial tool-using card",
    }
    return InvokeRequest(
        snapshot=CardSnapshot(card_id="bTST-99-tool", frontmatter=fm, body="do the thing"),
        worktree=tmp_path,
        attempt_trace_id="att-tool",
        trace_id="trace-tool",
    )


# --- tests -----------------------------------------------------------


def test_tool_loop_runs_one_file_write_then_report_done(tmp_path: Path) -> None:
    client = _FakeClient([
        _FakeMessage(
            [
                _FakeTextBlock("writing the file"),
                _FakeToolUseBlock(
                    id="tu1", name="file_write",
                    input={"path": "out.txt", "content": "hello"},
                ),
            ],
        ),
        _FakeMessage(
            [
                _FakeToolUseBlock(
                    id="tu2", name="report_done",
                    input={"summary": "wrote out.txt", "confidence": 0.97},
                ),
            ],
            stop_reason="tool_use",
        ),
    ])
    inv = SdkInvoker(client=client, api_key="fake", use_tools=True, tool_env={})
    result = inv.invoke(_request(tmp_path))
    assert result.success is True
    assert result.halt_kind is None
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"
    assert "wrote out.txt" in result.completion_notes_markdown
    assert "tool calls: 2" in result.completion_notes_markdown
    assert len(client.messages.calls) == 2


def test_tool_loop_settles_on_pure_text_turn(tmp_path: Path) -> None:
    # Turn 1 invokes a tool; turn 2 returns only text. The invoker
    # treats the bare-text turn as "settle" with the missing-marker
    # default (high confidence).
    client = _FakeClient([
        _FakeMessage([
            _FakeToolUseBlock(
                id="tu1", name="list_dir", input={"path": "."}
            ),
        ]),
        _FakeMessage([_FakeTextBlock("nothing left to do.")], stop_reason="end_turn"),
    ])
    inv = SdkInvoker(client=client, api_key="fake", use_tools=True, tool_env={})
    result = inv.invoke(_request(tmp_path))
    assert result.success is True
    assert result.halt_kind is None


def test_tool_loop_low_confidence_climbs_to_sonnet(tmp_path: Path) -> None:
    # Haiku attempt reports low confidence via report_done; one
    # escalation, the sonnet attempt settles.
    client = _FakeClient([
        _FakeMessage([
            _FakeToolUseBlock(
                id="tu_a", name="report_done",
                input={"summary": "shaky", "confidence": 0.3},
            ),
        ]),
        _FakeMessage([
            _FakeToolUseBlock(
                id="tu_b", name="report_done",
                input={"summary": "sonnet nails it", "confidence": 0.95},
            ),
        ]),
    ])
    inv = SdkInvoker(client=client, api_key="fake", use_tools=True, tool_env={})
    result = inv.invoke(_request(tmp_path))
    assert result.success is True
    assert result.halt_kind is None
    assert len(result.cascade_history) == 1
    assert result.cascade_history[0]["from_tier"] == 2
    assert result.cascade_history[0]["to_tier"] == 3
    assert result.model_used == "claude-sonnet-4-6"


def test_tool_loop_refused_tool_does_not_crash_the_loop(tmp_path: Path) -> None:
    # The model asks for a forbidden git verb; the dispatcher returns
    # a refusal-shaped tool_result and the loop continues.
    client = _FakeClient([
        _FakeMessage([
            _FakeToolUseBlock(
                id="tu1", name="git", input={"args": ["push", "origin", "main"]},
            ),
        ]),
        _FakeMessage([
            _FakeToolUseBlock(
                id="tu2", name="report_done",
                input={"summary": "no remote ops needed", "confidence": 0.9},
            ),
        ]),
    ])
    inv = SdkInvoker(client=client, api_key="fake", use_tools=True, tool_env={})
    result = inv.invoke(_request(tmp_path))
    assert result.success is True
    # The git tool was refused, so the first tool call shows ok=False
    # in the log; but the overall executor finish is clean.
    assert "git (FAIL)" in result.completion_notes_markdown


def test_tool_loop_cost_cap_halts_mid_loop(tmp_path: Path) -> None:
    # The first turn's call records a giant token count, breaching the
    # cap; the post-call hook raises and the executor halts.
    client = _FakeClient([
        _FakeMessage(
            [_FakeToolUseBlock(id="tu1", name="list_dir", input={"path": "."})],
            input_tokens=10_000_000,
            output_tokens=10,
        ),
    ])
    inv = SdkInvoker(client=client, api_key="fake", use_tools=True, tool_env={})
    result = inv.invoke(_request(tmp_path, cost_cap=0.05))
    assert result.halt_kind == "cost_cap"
    assert result.success is False


def test_tool_loop_max_turns_caps_runaway(tmp_path: Path) -> None:
    # The model never calls report_done; the loop should stop after
    # max_tool_turns rather than spinning forever.
    runaway = [
        _FakeMessage([
            _FakeToolUseBlock(
                id=f"tu{i}", name="list_dir", input={"path": "."},
            )
        ])
        for i in range(50)
    ]
    client = _FakeClient(runaway)
    inv = SdkInvoker(
        client=client, api_key="fake",
        use_tools=True, max_tool_turns=4, tool_env={},
    )
    result = inv.invoke(_request(tmp_path))
    # The fake client serves 4 turns then the loop ends; the cascade
    # then evaluates -- because confidence fell back to the missing
    # default (1.0), it settles without escalating. The model_used
    # records the cascade step it ended on.
    assert len(client.messages.calls) == 4
    assert result.success is True


def test_tool_descriptors_passed_to_create(tmp_path: Path) -> None:
    client = _FakeClient([
        _FakeMessage([
            _FakeToolUseBlock(
                id="tu1", name="report_done",
                input={"summary": "ok", "confidence": 0.9},
            ),
        ]),
    ])
    inv = SdkInvoker(client=client, api_key="fake", use_tools=True, tool_env={})
    inv.invoke(_request(tmp_path))
    call = client.messages.calls[0]
    assert "tools" in call
    tool_names = {t["name"] for t in call["tools"]}
    assert "file_read" in tool_names and "report_done" in tool_names
