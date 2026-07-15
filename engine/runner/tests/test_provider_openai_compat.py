"""OpenAI-compatible adapter -- pure, network-free unit tests.

The adapter is injected with a fake `post_json` transport, so the whole
file runs with zero network and zero tokens. This adapter is the one
that covers OpenAI, vLLM, Ollama, and (later) TensorRT-LLM -- anything
that speaks the OpenAI /v1/chat/completions schema.
"""
from __future__ import annotations

from typing import Any

from cards_runner.providers import CompletionRequest
from cards_runner.providers.openai_compat import OpenAICompatAdapter


class _FakePost:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        self.calls.append(
            {"url": url, "headers": headers, "body": body, "timeout": timeout}
        )
        return self.response


def test_openai_compat_completes_and_reports_usage() -> None:
    fake = _FakePost(
        {
            "choices": [{"message": {"content": "hello from qwen"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        }
    )
    adapter = OpenAICompatAdapter(
        base_url="http://localhost:11434/v1", api_key=None, post_json=fake
    )
    result = adapter.complete(
        CompletionRequest(
            model="qwen3:30b", system="sys", user="do it", max_output_tokens=256
        )
    )
    assert result.text == "hello from qwen"
    assert result.input_tokens == 12
    assert result.output_tokens == 7

    call = fake.calls[0]
    assert call["url"] == "http://localhost:11434/v1/chat/completions"
    assert call["body"]["model"] == "qwen3:30b"
    assert call["body"]["max_tokens"] == 256
    assert call["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do it"},
    ]
    # Keyless local endpoint sends no Authorization header.
    assert "Authorization" not in call["headers"]


def test_openai_compat_sends_bearer_when_key_present() -> None:
    fake = _FakePost({"choices": [{"message": {"content": "x"}}], "usage": {}})
    adapter = OpenAICompatAdapter(
        base_url="https://api.openai.com/v1", api_key="sk-secret", post_json=fake
    )
    adapter.complete(
        CompletionRequest(model="gpt-5", system="s", user="u", max_output_tokens=10)
    )
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-secret"


def test_openai_compat_strips_trailing_slash_on_base_url() -> None:
    fake = _FakePost({"choices": [{"message": {"content": "x"}}], "usage": {}})
    adapter = OpenAICompatAdapter(
        base_url="http://host:8000/v1/", api_key=None, post_json=fake
    )
    adapter.complete(
        CompletionRequest(model="m", system="s", user="u", max_output_tokens=10)
    )
    assert fake.calls[0]["url"] == "http://host:8000/v1/chat/completions"


def test_openai_compat_tolerates_missing_usage_and_content() -> None:
    fake = _FakePost({"choices": [{"message": {}}]})
    adapter = OpenAICompatAdapter(base_url="http://x/v1", api_key=None, post_json=fake)
    result = adapter.complete(
        CompletionRequest(model="m", system="s", user="u", max_output_tokens=10)
    )
    assert result.text == ""
    assert result.input_tokens == 0
    assert result.output_tokens == 0


def test_openai_compat_name_is_stable() -> None:
    adapter = OpenAICompatAdapter(base_url="http://x/v1", api_key=None)
    assert adapter.name == "openai_compat"
