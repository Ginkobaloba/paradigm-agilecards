"""OpenAI-compatible chat adapter.

One adapter for every provider that speaks the OpenAI
`/v1/chat/completions` schema: OpenAI itself, vLLM, Ollama (its `/v1`
surface), Azure OpenAI, and TensorRT-LLM behind a Triton OpenAI
frontend. They differ only by base_url and key, which the registry
resolves; this class is provider-neutral.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .base import (
    DEFAULT_TIMEOUT_S,
    AssistantTurn,
    CompletionRequest,
    CompletionResult,
    Message,
    PostJson,
    ToolCall,
    ToolResults,
    ToolSpec,
    ToolTurnRequest,
    ToolTurnResult,
    UserText,
    urllib_post_json,
)


@dataclass
class OpenAICompatAdapter:
    base_url: str
    api_key: str | None
    post_json: PostJson = field(default=urllib_post_json)
    timeout: float = DEFAULT_TIMEOUT_S
    name: str = "openai_compat"

    def complete(self, req: CompletionRequest) -> CompletionResult:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_output_tokens,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
        }
        data = self.post_json(url, headers=headers, body=body, timeout=self.timeout)
        return CompletionResult(
            text=_extract_text(data),
            input_tokens=_usage(data, "prompt_tokens"),
            output_tokens=_usage(data, "completion_tokens"),
        )

    def tool_turn(self, req: ToolTurnRequest) -> ToolTurnResult:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_output_tokens,
            "messages": _to_openai_messages(req.system, req.messages),
        }
        if req.tools:
            body["tools"] = [_to_openai_tool(t) for t in req.tools]
        data = self.post_json(url, headers=headers, body=body, timeout=self.timeout)
        return _parse_tool_turn(data)


def _extract_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def _usage(data: Any, key: str) -> int:
    if not isinstance(data, dict):
        return 0
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


# ---- tool-use translation (KL3) --------------------------------------


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _to_openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if isinstance(m, UserText):
            out.append({"role": "user", "content": m.text})
        elif isinstance(m, AssistantTurn):
            msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            out.append(msg)
        elif isinstance(m, ToolResults):
            for r in m.results:
                out.append(
                    {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                )
    return out


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_tool_turn(data: Any) -> ToolTurnResult:
    if not isinstance(data, dict):
        return ToolTurnResult(
            text="", tool_calls=(), input_tokens=0, output_tokens=0, finished=True
        )
    choices = data.get("choices")
    message: Any = None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
    message = message if isinstance(message, dict) else {}

    content = message.get("content")
    text = content.strip() if isinstance(content, str) else ""

    calls: list[ToolCall] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for rc in raw_calls:
            if not isinstance(rc, dict):
                continue
            fn = rc.get("function")
            fn = fn if isinstance(fn, dict) else {}
            calls.append(
                ToolCall(
                    id=str(rc.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=_parse_args(fn.get("arguments")),
                )
            )
    return ToolTurnResult(
        text=text,
        tool_calls=tuple(calls),
        input_tokens=_usage(data, "prompt_tokens"),
        output_tokens=_usage(data, "completion_tokens"),
        finished=len(calls) == 0,
    )
