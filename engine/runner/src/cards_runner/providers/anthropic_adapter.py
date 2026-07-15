"""Anthropic adapter.

Wraps the Anthropic Messages API behind the same port as the others.
`client` is injectable (tests pass a fake exposing `.messages.create`),
which is exactly how `SdkInvoker` already ran token-free -- this adapter
is a lift of that call path, not a new integration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import CompletionRequest, CompletionResult


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


def _text_of(message: Any) -> str:
    """Join every text block of an Anthropic Message into one string."""
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts).strip()
