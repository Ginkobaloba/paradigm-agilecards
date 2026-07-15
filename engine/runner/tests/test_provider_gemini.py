"""Gemini adapter -- network-free unit tests via an injected transport."""
from __future__ import annotations

from typing import Any

from cards_runner.providers import CompletionRequest
from cards_runner.providers.gemini_adapter import GeminiAdapter


class _FakePost:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, *, headers: dict[str, str], body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.response


def test_gemini_completes_joins_parts_and_reports_usage() -> None:
    fake = _FakePost(
        {
            "candidates": [{"content": {"parts": [{"text": "g1"}, {"text": "g2"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        }
    )
    adapter = GeminiAdapter(api_key="k", post_json=fake)
    result = adapter.complete(
        CompletionRequest(
            model="gemini-2.0-flash", system="sys", user="u", max_output_tokens=64
        )
    )
    assert result.text == "g1\ng2"
    assert result.input_tokens == 5
    assert result.output_tokens == 3


def test_gemini_key_in_header_not_url() -> None:
    fake = _FakePost({"candidates": [], "usageMetadata": {}})
    adapter = GeminiAdapter(api_key="secret-key", post_json=fake)
    adapter.complete(
        CompletionRequest(model="gemini-2.0-flash", system="s", user="u", max_output_tokens=8)
    )
    call = fake.calls[0]
    assert "models/gemini-2.0-flash:generateContent" in call["url"]
    assert "secret-key" not in call["url"]  # never in the URL/query
    assert call["headers"]["x-goog-api-key"] == "secret-key"
    assert call["body"]["systemInstruction"]["parts"][0]["text"] == "s"
    assert call["body"]["contents"][0]["parts"][0]["text"] == "u"


def test_gemini_tolerates_empty_response() -> None:
    fake = _FakePost({})
    adapter = GeminiAdapter(api_key="k", post_json=fake)
    result = adapter.complete(
        CompletionRequest(model="m", system="s", user="u", max_output_tokens=8)
    )
    assert result.text == ""
    assert result.input_tokens == 0
    assert result.output_tokens == 0
