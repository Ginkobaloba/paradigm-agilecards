"""Provider selection + endpoint resolution -- pure unit tests.

`split_model` maps a (possibly provider-prefixed) model id to a
(provider, raw_tag) pair; `build_adapter` turns that into a concrete
adapter with its endpoint resolved from env (with safe defaults).
"""
from __future__ import annotations

from typing import Any

import pytest

from cards_runner.providers import registry


def test_split_model_recognizes_provider_prefixes() -> None:
    assert registry.split_model("ollama/qwen3:30b") == ("ollama", "qwen3:30b")
    assert registry.split_model("vllm/qwen3") == ("vllm", "qwen3")
    assert registry.split_model("openai/gpt-5") == ("openai", "gpt-5")
    assert registry.split_model("azure/gpt-4o") == ("azure", "gpt-4o")
    assert registry.split_model("local/whatever") == ("local", "whatever")
    assert registry.split_model("gemini/gemini-2.0-flash") == (
        "gemini",
        "gemini-2.0-flash",
    )
    assert registry.split_model("anthropic/claude-opus-4-7") == (
        "anthropic",
        "claude-opus-4-7",
    )


def test_split_model_unprefixed_claude_is_anthropic_sent_as_is() -> None:
    # Backward compatibility: the claude tier map ships unprefixed ids.
    assert registry.split_model("claude-haiku-4-5-20251001") == (
        "anthropic",
        "claude-haiku-4-5-20251001",
    )


def test_build_adapter_routes_ollama_to_openai_compat_with_default_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CARDS_RUNNER_OLLAMA_BASE_URL", raising=False)

    def _fake(url: str, *, headers: Any, body: Any, timeout: float) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    adapter, tag = registry.build_adapter("ollama/qwen3:30b", post_json=_fake)
    assert tag == "qwen3:30b"
    assert adapter.name == "openai_compat"
    # Ollama's local default endpoint, no key required.
    assert adapter.base_url == "http://localhost:11434/v1"
    assert adapter.api_key is None


def test_build_adapter_reads_endpoint_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARDS_RUNNER_VLLM_BASE_URL", "http://drewspc:8000/v1")
    monkeypatch.setenv("CARDS_RUNNER_VLLM_API_KEY", "tok")
    adapter, tag = registry.build_adapter("vllm/qwen3")
    assert tag == "qwen3"
    assert adapter.base_url == "http://drewspc:8000/v1"
    assert adapter.api_key == "tok"


def test_build_adapter_routes_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARDS_RUNNER_GEMINI_API_KEY", "gk")
    adapter, tag = registry.build_adapter("gemini/gemini-2.0-flash")
    assert tag == "gemini-2.0-flash"
    assert adapter.name == "gemini"
    assert adapter.api_key == "gk"


def test_build_adapter_anthropic_uses_injected_client() -> None:
    sentinel = object()
    adapter, tag = registry.build_adapter(
        "claude-haiku-4-5-20251001", anthropic_client=sentinel
    )
    assert tag == "claude-haiku-4-5-20251001"
    assert adapter.name == "anthropic"
    assert adapter.client is sentinel
