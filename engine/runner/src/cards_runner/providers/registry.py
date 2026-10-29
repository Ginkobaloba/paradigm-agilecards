"""Provider selection and endpoint resolution.

A model id names its provider by prefix (`ollama/qwen3:30b`,
`gemini/...`, `anthropic/claude-...`); an unprefixed id is treated as
Anthropic so the existing claude tier map keeps working untouched.
Endpoints and keys for the HTTP providers come from env (externalized
secrets), with safe defaults for the well-known local ones.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from .anthropic_adapter import AnthropicAdapter
from .base import PostJson, ProviderAdapter, urllib_post_json
from .gemini_adapter import GeminiAdapter
from .openai_compat import OpenAICompatAdapter


# Prefix -> provider name. Order does not matter (prefixes are disjoint).
_PREFIX_TO_PROVIDER: dict[str, str] = {
    "anthropic/": "anthropic",
    "claude/": "anthropic",
    "gemini/": "gemini",
    "google/": "gemini",
    "openai/": "openai",
    "ollama/": "ollama",
    "vllm/": "vllm",
    "azure/": "azure",
    "local/": "local",
}

# Providers served by the one OpenAI-compatible adapter.
_OPENAI_COMPAT_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "ollama", "vllm", "azure", "local"}
)

# Endpoint defaults. `None` means "must come from env" (no safe default).
_DEFAULT_BASE_URL: dict[str, str | None] = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": None,
    "azure": None,
    "local": None,
}


class ProviderConfigError(RuntimeError):
    """Raised when a provider is selected but its endpoint/key is unset."""


def split_model(model_id: str) -> tuple[str, str]:
    """Return `(provider, raw_tag)`; unprefixed ids resolve to anthropic."""
    low = model_id.lower()
    for prefix, provider in _PREFIX_TO_PROVIDER.items():
        if low.startswith(prefix):
            return provider, model_id[len(prefix):]
    return "anthropic", model_id


# Providers a worker can call with no credential at all. openai, gemini
# and azure are NOT here: they need a key the spawner never forwards, so a
# map using them is not keyless even though it is not Anthropic.
KEYLESS_LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama", "vllm", "local"})

# Their endpoints, the only provider env the spawner forwards to a worker.
LOCAL_BASE_URL_ENV: tuple[str, ...] = tuple(
    f"CARDS_RUNNER_{p.upper()}_BASE_URL" for p in sorted(KEYLESS_LOCAL_PROVIDERS)
)


def tiers_needing_a_key(points_to_model: Mapping[int, str]) -> list[tuple[int, str, str]]:
    """The `(points, model_id, provider)` tiers a keyless worker cannot run.

    Empty means every tier routes to a keyless local provider, so the map
    can start without ANTHROPIC_API_KEY. Decided by `split_model`, the same
    routing `build_adapter` uses, so this cannot drift from where a call
    actually goes.
    """
    hits = []
    for points, model in sorted(points_to_model.items()):
        provider = split_model(model)[0]
        if provider not in KEYLESS_LOCAL_PROVIDERS:
            hits.append((points, model, provider))
    return hits


_HOST_PORT = re.compile(r"^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^\[[0-9A-Fa-f:.]+\](:[0-9]{1,5})?$")


def base_url_problem(url: str) -> str | None:
    """Why `url` must not be passed to a worker, or None if it is clean.

    An ALLOWLIST, not a list of suspicious names: a base URL is forwarded
    to the worker verbatim, past the env scrub that exists to keep
    credentials out, and a local endpoint is only ever
    `http(s)://host[:port][/path]`. So any userinfo, query, fragment or
    `;` parameter is refused outright, whatever it is called (key, jwt,
    bearer, sas, ...). The reasons never quote the URL.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "is not a parseable URL"
    if parts.scheme not in ("http", "https"):
        return "must use http or https"
    if "@" in parts.netloc:
        return "must not carry userinfo"
    if not _HOST_PORT.match(parts.netloc):
        return "must name only a host and optional port"
    if parts.query or "?" in url:
        return "must not carry a query string"
    if parts.fragment or "#" in url:
        return "must not carry a fragment"
    if ";" in parts.path:
        return "must not carry ';' parameters"
    return None


def _env(provider: str, suffix: str) -> str | None:
    return os.environ.get(f"CARDS_RUNNER_{provider.upper()}_{suffix}")


def resolve_endpoint(provider: str) -> tuple[str | None, str | None]:
    """`(base_url, api_key)` for an OpenAI-compatible provider."""
    base_url = _env(provider, "BASE_URL") or _DEFAULT_BASE_URL.get(provider)
    return base_url, _env(provider, "API_KEY")


def build_adapter(
    model_id: str,
    *,
    anthropic_client: Any | None = None,
    post_json: PostJson = urllib_post_json,
) -> tuple[ProviderAdapter, str]:
    """Resolve `model_id` to a concrete adapter and its raw model tag."""
    provider, tag = split_model(model_id)

    if provider == "anthropic":
        return AnthropicAdapter(client=anthropic_client), tag

    if provider == "gemini":
        key = (
            _env("gemini", "API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not key:
            raise ProviderConfigError(
                "gemini selected but no API key (set CARDS_RUNNER_GEMINI_API_KEY)"
            )
        return GeminiAdapter(api_key=key, post_json=post_json), tag

    if provider in _OPENAI_COMPAT_PROVIDERS:
        base_url, api_key = resolve_endpoint(provider)
        if not base_url:
            raise ProviderConfigError(
                f"{provider} selected but no base_url "
                f"(set CARDS_RUNNER_{provider.upper()}_BASE_URL)"
            )
        return (
            OpenAICompatAdapter(
                base_url=base_url, api_key=api_key, post_json=post_json
            ),
            tag,
        )

    raise ProviderConfigError(f"unknown provider {provider!r} for model {model_id!r}")
