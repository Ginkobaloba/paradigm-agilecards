"""OpenAI-compatible chat adapter.

One adapter for every provider that speaks the OpenAI
`/v1/chat/completions` schema: OpenAI itself, vLLM, Ollama (its `/v1`
surface), Azure OpenAI, and TensorRT-LLM behind a Triton OpenAI
frontend. They differ only by base_url and key, which the registry
resolves; this class is provider-neutral.
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
