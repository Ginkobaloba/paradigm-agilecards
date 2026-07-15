"""Provider-agnostic LLM adapter port for the card executor.

Three adapters cover five-plus providers: `anthropic`, `gemini`, and one
`openai_compat` adapter for OpenAI / vLLM / Ollama / Azure / TensorRT-LLM.
Selection is by model-id prefix (see `registry.build_adapter`).
"""
from __future__ import annotations

from .base import (
    CompletionRequest,
    CompletionResult,
    PostJson,
    ProviderAdapter,
    urllib_post_json,
)
from .registry import ProviderConfigError, build_adapter, split_model

__all__ = [
    "CompletionRequest",
    "CompletionResult",
    "PostJson",
    "ProviderAdapter",
    "ProviderConfigError",
    "build_adapter",
    "split_model",
    "urllib_post_json",
]
