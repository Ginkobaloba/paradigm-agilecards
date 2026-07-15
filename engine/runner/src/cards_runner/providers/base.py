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


# ---- tool-use (KL3) --------------------------------------------------
#
# A neutral tool vocabulary so the multi-turn tool loop is written once
# and each adapter translates to/from its provider's wire format. The
# representation is deliberately generic -- a "tool" is a name + JSON
# schema + (elsewhere) an executor -- so a future non-code tool belt
# (e.g. a fabrication card type) plugs into the same port unchanged.


@dataclass(frozen=True)
class ToolSpec:
    """A callable tool offered to the model. `parameters` is JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A model's request to call one tool."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResultMsg:
    """The outcome of executing one `ToolCall`, fed back to the model."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserText:
    """A plain user message."""

    text: str


@dataclass(frozen=True)
class AssistantTurn:
    """A model turn: free text plus zero or more tool-call requests."""

    text: str
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class ToolResults:
    """A batch of tool outcomes returned to the model as one turn."""

    results: tuple[ToolResultMsg, ...]


# The neutral conversation element. The loop owns the history; adapters
# are stateless and translate the whole history on each turn.
Message = UserText | AssistantTurn | ToolResults


@dataclass(frozen=True)
class ToolTurnRequest:
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_output_tokens: int


@dataclass(frozen=True)
class ToolTurnResult:
    """One tool-use turn, normalized across providers.

    `finished` is True when the model produced no tool calls (it ended
    its turn), which the loop reads as "settle / stop".
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    input_tokens: int
    output_tokens: int
    finished: bool


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
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    # A well-behaved provider returns a JSON object; anything else (a
    # top-level array, string, etc. from a broken server) normalizes to
    # {} so the return type is honest and adapters degrade gracefully.
    return parsed if isinstance(parsed, dict) else {}
