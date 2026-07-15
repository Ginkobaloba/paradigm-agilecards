"""The provider adapter port.

One narrow primitive -- "complete one turn" -- behind a uniform
interface, so the executor's model call is provider-agnostic. Adapters
live beside this file; selection lives in `registry.py`.

The HTTP transport is injected (`PostJson`) so adapters are pure and
network-free under test. The production default is a stdlib-urllib
transport -- no third-party HTTP dependency is added to the runner.

Tool-use is deliberately NOT part of this port yet; KL2 covers
reasoning-only completion across providers. The multi-turn tool loop
grows onto the port in KL3.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


# The runner churns to completion, not interactively; latency is a
# non-goal and a local 30B can take minutes per turn. So the default
# transport timeout is generous. Callers may override per adapter.
DEFAULT_TIMEOUT_S: float = 600.0


@dataclass(frozen=True)
class CompletionRequest:
    """Everything an adapter needs for one reasoning-only turn.

    `model` is the raw provider tag (any provider prefix already
    stripped by the registry) -- e.g. `qwen3:30b`, not `ollama/qwen3:30b`.
    """

    model: str
    system: str
    user: str
    max_output_tokens: int


@dataclass(frozen=True)
class CompletionResult:
    """What every adapter returns, normalized across providers."""

    text: str
    input_tokens: int
    output_tokens: int


class ProviderAdapter(Protocol):
    """Strategy for making one model call against a provider."""

    name: str

    def complete(self, req: CompletionRequest) -> CompletionResult: ...


class PostJson(Protocol):
    """Injected HTTP transport: POST a JSON body, return the parsed JSON."""

    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]: ...


def urllib_post_json(
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """The production transport: a dependency-free POST of a JSON body.

    Kept intentionally minimal. Adapters own request/response shaping;
    this only moves bytes. HTTP errors surface as the exception the
    invoker's try/except already maps to an executor error result.
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}
