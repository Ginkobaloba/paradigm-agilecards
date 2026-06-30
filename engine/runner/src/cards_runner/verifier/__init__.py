"""Cold-read verifier (RUNNER_CONTRACT.md "Cold-read verification").

Chunk 3 deliverable. The verifier is the gate that moves a clean
`active` card to `done`. It is a **structured runner**, not a single
per-card reasoning agent: every acceptance-criterion item is
dispatched by its `type:` to a handler. Deterministic items (file
existence, file content, shell exit code) execute in pure Python and
cost zero LLM tokens. Subjective items (`type: subjective`) are
batched into one cascading evaluator call.

What the contract requires (paraphrasing the relevant sections of
RUNNER_CONTRACT.md):

- Two-path model: deterministic then subjective. The subjective phase
  runs at most once and may cascade haiku -> sonnet -> opus on
  confidence below `subjective_confidence_threshold` (default 0.85).
- Result shape: `VerifierResult(overall_status, items,
  cascade_history_appendix, standup_reason_items)`.
- Verdict handling by the daemon:
  - `pass`     -> stamp provenance, move to `done`
  - `fail`     -> append `verifier_notes`, return to backlog
  - `needs_standup_review` -> route to `awaiting_standup_review`
  - `error` (orchestrator crash, after two retries) -> `blocked`

What this module is NOT:

- It does not modify the store -- only reads card snapshots and
  produces `VerifierResult`. The daemon owns the resulting transition.
- It does not enforce verifier-skip eligibility -- the daemon does
  (the skip decision needs cascade history and project config that
  live above the verifier). The verifier always runs every applicable
  handler when called.

The package layout mirrors the canonical /cards skill library that
RUNNER_CONTRACT.md references (`lib/verifier/`). Keeping the names
aligned now is the cheap option for the day the canonical library is
vendored in.
"""
from __future__ import annotations

from .risk_factor import RiskFactor, parse_risk_factors
from .runner import (
    HandlerResult,
    ItemResult,
    VerifierError,
    VerifierResult,
    verify_card,
)
from .types import CANONICAL_TYPES, SchemaError


__all__ = [
    "CANONICAL_TYPES",
    "HandlerResult",
    "ItemResult",
    "RiskFactor",
    "SchemaError",
    "VerifierError",
    "VerifierResult",
    "parse_risk_factors",
    "verify_card",
]
