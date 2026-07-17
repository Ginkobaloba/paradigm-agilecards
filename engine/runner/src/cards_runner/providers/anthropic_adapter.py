"""Anthropic adapter.

Wraps the Anthropic Messages API behind the same port as the others.
`client` is injectable (tests pass a fake exposing `.messages.create`),
which is exactly how `SdkInvoker` already ran token-free -- this adapter
is a lift of that call path, not a new integration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import (
    AssistantTurn,
    CompletionRequest,
    CompletionResult,
    Message,
    ToolCall,
    ToolResults,
    ToolSpec,
    ToolTurnRequest,
    ToolTurnResult,
    UserText,
)


@dataclass
class AnthropicAdapter:
    client: Any  # anthropic.Anthropic, or a fake with .messages.create
    name: str = "anthropic"

    def complete(self, req: CompletionRequest) -> CompletionResult:
        message = self.client.messages.create(
            model=req.model,
            max_tokens=req.max_output_tokens,
            system=req.system,
            messages=[{"role": "user", "content": req.user}],
        )
        usage = getattr(message, "usage", None)
        return CompletionResult(
            text=_text_of(message),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )

    def tool_turn(self, req: ToolTurnRequest) -> ToolTurnResult:
        kwargs: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_output_tokens,
            "system": req.system,
            "messages": _to_anthropic_messages(req.messages),
        }
        if req.tools:
            kwargs["tools"] = [_to_anthropic_tool(t) for t in req.tools]
        message = self.client.messages.create(**kwargs)

        calls: list[ToolCall] = []
        for block in getattr(message, "content", []) or []:
            if _block_type(block) != "tool_use":
                continue
            raw_input = _block_attr(block, "input")
            calls.append(
                ToolCall(
                    id=str(_block_attr(block, "id") or ""),
                    name=str(_block_attr(block, "name") or ""),
                    arguments=raw_input if isinstance(raw_input, dict) else {},
                )
            )
        usage = getattr(message, "usage", None)
        return ToolTurnResult(
            text=_text_of(message),
            tool_calls=tuple(calls),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            finished=len(calls) == 0,
        )


def _text_of(message: Any) -> str:
    """Join every text block of an Anthropic Message into one string."""
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts).strip()


# ---- tool-use translation (KL3) --------------------------------------
#
# Blocks arrive as SDK objects in production and as dicts from some
# fakes/round-trips, so every accessor handles both shapes.


def _block_type(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type", ""))
    return str(getattr(block, "type", "") or "")


def _block_attr(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _to_anthropic_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.parameters,
    }


def _to_anthropic_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, UserText):
            out.append({"role": "user", "content": m.text})
        elif isinstance(m, AssistantTurn):
            blocks: list[dict[str, Any]] = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            for tc in m.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            out.append({"role": "assistant", "content": blocks})
        elif isinstance(m, ToolResults):
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.tool_call_id,
                            "is_error": r.is_error,
                            "content": r.content,
                        }
                        for r in m.results
                    ],
                }
            )
    return out
