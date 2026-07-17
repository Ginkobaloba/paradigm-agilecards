"""Google Gemini adapter.

The one provider whose wire schema is not OpenAI-compatible:
`:generateContent` with `systemInstruction` / `contents` / `parts`.
The API key goes in the `x-goog-api-key` header (never the URL query
string, so it stays out of access logs).
"""
from __future__ import annotations

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

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


@dataclass
class GeminiAdapter:
    api_key: str
    base_url: str = _DEFAULT_BASE_URL
    post_json: PostJson = field(default=urllib_post_json)
    timeout: float = DEFAULT_TIMEOUT_S
    name: str = "gemini"

    def complete(self, req: CompletionRequest) -> CompletionResult:
        url = f"{self.base_url.rstrip('/')}/models/{req.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": [{"text": req.user}]}],
            "generationConfig": {"maxOutputTokens": req.max_output_tokens},
        }
        data = self.post_json(url, headers=headers, body=body, timeout=self.timeout)
        usage = data.get("usageMetadata") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return CompletionResult(
            text=_extract_text(data),
            input_tokens=_int(usage.get("promptTokenCount")),
            output_tokens=_int(usage.get("candidatesTokenCount")),
        )

    def tool_turn(self, req: ToolTurnRequest) -> ToolTurnResult:
        url = f"{self.base_url.rstrip('/')}/models/{req.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": _to_gemini_contents(req.messages),
            "generationConfig": {"maxOutputTokens": req.max_output_tokens},
        }
        if req.tools:
            body["tools"] = [
                {"functionDeclarations": [_to_gemini_tool(t) for t in req.tools]}
            ]
        data = self.post_json(url, headers=headers, body=body, timeout=self.timeout)

        usage = data.get("usageMetadata") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        calls = _extract_function_calls(data)
        return ToolTurnResult(
            text=_extract_text(data),
            tool_calls=calls,
            input_tokens=_int(usage.get("promptTokenCount")),
            output_tokens=_int(usage.get("candidatesTokenCount")),
            finished=len(calls) == 0,
        )


def _extract_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""
    texts = [
        str(p.get("text", ""))
        for p in parts
        if isinstance(p, dict) and isinstance(p.get("text"), str)
    ]
    return "\n".join(texts).strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---- tool-use translation (KL3) --------------------------------------


def _to_gemini_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
    }


def _to_gemini_contents(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, UserText):
            out.append({"role": "user", "parts": [{"text": m.text}]})
        elif isinstance(m, AssistantTurn):
            parts: list[dict[str, Any]] = []
            if m.text:
                parts.append({"text": m.text})
            for tc in m.tool_calls:
                parts.append(
                    {"functionCall": {"name": tc.name, "args": tc.arguments}}
                )
            out.append({"role": "model", "parts": parts})
        elif isinstance(m, ToolResults):
            # Gemini keys results by tool NAME, not by a call id (it has
            # none on the wire), so the neutral id is dropped here.
            out.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": r.name,
                                "response": {"content": r.content},
                            }
                        }
                        for r in m.results
                    ],
                }
            )
    return out


def _parts_of(data: Any) -> list[Any]:
    if not isinstance(data, dict):
        return []
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return []
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    return parts if isinstance(parts, list) else []


def _extract_function_calls(data: Any) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for idx, part in enumerate(_parts_of(data)):
        if not isinstance(part, dict):
            continue
        fc = part.get("functionCall")
        if not isinstance(fc, dict):
            continue
        args = fc.get("args")
        name = str(fc.get("name") or "")
        calls.append(
            ToolCall(
                # Gemini sends no call id; synthesize a stable one so the
                # neutral loop can correlate call -> result.
                id=f"gemini_{idx}_{name}",
                name=name,
                arguments=args if isinstance(args, dict) else {},
            )
        )
    return tuple(calls)
