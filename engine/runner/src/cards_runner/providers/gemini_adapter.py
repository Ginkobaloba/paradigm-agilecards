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
    CompletionRequest,
    CompletionResult,
    PostJson,
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
