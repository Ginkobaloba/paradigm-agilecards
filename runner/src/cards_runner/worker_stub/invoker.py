"""The Invoker seam.

The `Invoker` abstracts "do whatever the executor does to drive the
card forward." Chunk 1 ships a `StubInvoker` that does nothing but
sleep and return a fake completion. Chunk 2 will land an
`SdkInvoker` that opens an Anthropic client in-process, runs the
executor protocol, and reports usage via the SDK's response
metadata. A future ensemble executor (multiple SDKs, debate-style
agents) can plug in here without modifying the daemon or the worker
runner.

Keep the interface narrow on purpose: anything richer drags chunk 2
concerns (hooks, cost tally, cascade) into chunk 1 and bloats the
seam. The hook system from chunk 2 is mediated through the SDK
client itself, not through this interface.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..common.types import CardSnapshot


@dataclass(frozen=True)
class InvokeRequest:
    """Everything the Invoker needs to do its work."""

    snapshot: CardSnapshot
    worktree: Path
    attempt_trace_id: str
    trace_id: str


@dataclass(frozen=True)
class InvokeResult:
    """What the Invoker reports back to the worker runner.

    The first four fields are the chunk 1 contract; the rest are the
    chunk 2b-ii additions for the real executor. All new fields carry
    defaults, so `StubInvoker` (and any other chunk 1 caller) is
    untouched.

    `halt_kind` is `None` on a normal finish, `"cost_cap"` when the
    card's `cost_cap_usd` was hit, and `"cascade_exhausted"` when the
    confidence cascade climbed its two permitted tiers without
    reaching threshold. The worker maps it to an exit code and the
    daemon routes that exit code (see `worker.run_worker` and
    `daemon._post_worker_exit`).
    """

    completion_notes_markdown: str
    actual_tokens: int  # 0 in stub mode.
    model_used: str | None
    success: bool
    actual_cost_usd: float = 0.0
    halt_kind: str | None = None  # None | "cost_cap" | "cascade_exhausted"
    cascade_history: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    cost_snapshot: dict[str, Any] = field(default_factory=dict)


class Invoker(Protocol):
    """Strategy for executing a card.

    Implementations must be thread-safe-ish only in the sense that
    they run inside a single worker subprocess. The worker runner
    serializes calls; no concurrent invocation is expected.
    """

    def invoke(self, request: InvokeRequest) -> InvokeResult: ...


@dataclass
class StubInvoker:
    """Chunk 1 default. Sleeps `sleep_sec` then returns success.

    The sleep mimics real per-card work so heartbeat / orphan-reclaim
    paths get exercised under normal conditions. Tests inject a tiny
    `sleep_sec` to keep the suite fast.
    """

    sleep_sec: float = 3.0

    def invoke(self, request: InvokeRequest) -> InvokeResult:
        time.sleep(self.sleep_sec)
        notes = (
            "Stub executor (chunk 1): walked the card through the runner state "
            "machine without any LLM call.\n\n"
            f"- attempt_trace_id: {request.attempt_trace_id}\n"
            f"- trace_id: {request.trace_id}\n"
            f"- worktree: {request.worktree}\n"
            f"- card_id: {request.snapshot.card_id}\n"
            f"- sleep_sec: {self.sleep_sec}\n"
        )
        return InvokeResult(
            completion_notes_markdown=notes,
            actual_tokens=0,
            model_used=None,
            success=True,
        )
