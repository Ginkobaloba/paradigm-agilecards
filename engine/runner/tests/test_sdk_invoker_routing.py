"""SdkInvoker routes each turn through the provider port.

A claude model id still uses the injected Anthropic client (existing
behavior, preserved). A provider-prefixed model id (e.g. `ollama/...`)
routes through the OpenAI-compatible transport instead -- the whole
point of the provider port. Both paths are token-free here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cards_runner.common.canonical_config import load_tier_map
from cards_runner.common.types import CardSnapshot
from cards_runner.worker_stub import sdk_invoker as sdk_mod
from cards_runner.worker_stub.invoker import InvokeRequest
from cards_runner.worker_stub.sdk_invoker import SdkInvoker


class _FakePost:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, *, headers: dict[str, str], body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "body": body})
        return self.response


class _Usage:
    def __init__(self, i: int, o: int) -> None:
        self.input_tokens = i
        self.output_tokens = o


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Message:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]
        self.usage = _Usage(11, 22)


class _Messages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return _Message("claude answer")


class _Client:
    def __init__(self) -> None:
        self.messages = _Messages()


def test_local_model_routes_through_openai_compat(monkeypatch: Any) -> None:
    monkeypatch.delenv("CARDS_RUNNER_OLLAMA_BASE_URL", raising=False)
    fake = _FakePost(
        {
            "choices": [{"message": {"content": "local answer"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        }
    )
    inv = SdkInvoker(post_json=fake)
    text, in_tok, out_tok = inv._one_turn("ollama/qwen3:30b", "sys", "user")
    assert text == "local answer"
    assert in_tok == 3
    assert out_tok == 4
    # It hit the ollama endpoint with the provider prefix stripped.
    assert fake.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"
    assert fake.calls[0]["body"]["model"] == "qwen3:30b"


def test_claude_model_still_uses_anthropic_client() -> None:
    client = _Client()
    fake = _FakePost({"choices": [{"message": {"content": "should not be used"}}]})
    inv = SdkInvoker(client=client, post_json=fake)
    text, in_tok, out_tok = inv._one_turn("claude-haiku-4-5-20251001", "s", "u")
    assert text == "claude answer"
    assert in_tok == 11
    assert out_tok == 22
    # Anthropic path -- no HTTP transport touched.
    assert fake.calls == []
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5-20251001"


def _local_card(points: int = 1) -> InvokeRequest:
    fm: dict[str, Any] = {
        "points": points,
        "cost_cap_usd": None,
        "model_floor": "local",
        "title": "trivial local card",
    }
    snap = CardSnapshot(
        card_id="bLOC-01", frontmatter=fm, body="Trivial mechanical edit."
    )
    return InvokeRequest(
        snapshot=snap,
        worktree=Path("/tmp/wt"),
        attempt_trace_id="a",
        trace_id="t",
    )


def test_invoke_runs_a_card_free_on_a_local_endpoint(monkeypatch: Any) -> None:
    # KL1 + KL2 together: a reasoning-only card completes on an
    # OpenAI-compatible local endpoint, routed through the port, at $0.
    monkeypatch.delenv("CARDS_RUNNER_OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(sdk_mod, "_TIER_MAP_CACHE", load_tier_map(provider="local"))
    fake = _FakePost(
        {
            "choices": [{"message": {"content": "did the work\nCONFIDENCE: 0.95"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    inv = SdkInvoker(post_json=fake)
    result = inv.invoke(_local_card(points=1))
    assert result.success is True
    assert result.halt_kind is None
    assert result.model_used == "ollama/qwen3:30b"
    assert result.actual_cost_usd == 0.0  # local inference is free (KL1)
    assert result.actual_tokens == 15
    assert fake.calls and fake.calls[0]["body"]["model"] == "qwen3:30b"
