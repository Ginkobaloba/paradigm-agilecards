"""Result types shared by handlers and the orchestrator.

Kept in its own module so handlers can import the result types
without pulling in the runner (and the planner-side validator can
import them without pulling in the Anthropic SDK that the subjective
handler depends on).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ResultPhase = Literal["deterministic", "subjective"]


@dataclass(frozen=True)
class HandlerResult:
    """One handler's verdict on one AC item.

    `passed` is the only field that gates the card-level outcome.
    `evidence` is a free-form dict the handler populates with whatever
    a human or future agent would want to see on failure: stdout from
    a command, the offending HTTP body excerpt, the file path that
    didn't exist, etc. The runner serializes it into the card's
    `verifier_notes:` block.
    """

    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemResult:
    """The orchestrator-level wrapping of one item's outcome.

    `idx` is the original AC list index; the runner sorts by it to
    return results in declaration order regardless of which phase
    they ran in.
    """

    idx: int
    item: dict[str, Any]
    handler_result: HandlerResult
    phase: ResultPhase

    @property
    def passed(self) -> bool:
        return self.handler_result.passed
