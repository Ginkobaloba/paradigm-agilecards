"""agile-cards verifier library.

The verifier-as-structured-runner refactor (v1.3) splits cold-read
verification into two paths:

1. A deterministic path that runs every AC item declared as
   `file_exists`, `file_absent`, `file_contains`, `file_absent_content`,
   `command`, `python_assert`, `http_status`, or `http_contains`.
   Zero LLM tokens. Pure function call into the handler registered
   for that type.

2. A subjective path that runs at most once per card, only if the card
   has any `type: subjective` items. The subjective evaluator runs as
   a cascade haiku -> sonnet -> opus, escalating one tier any time the
   evaluator returns confidence below a configurable threshold. If
   opus cannot reach the threshold, the card lands in
   `awaiting_standup_review/` rather than auto-passing or auto-failing.

The public entry point is `verifier.runner.verify_card`. The schema
is exported via `verifier.schema.validate_ac_items` for planner-side
use at card write time.

The canonical type registry lives in `verifier.types`. Custom types
(project extensions) are not supported in v1.3 to keep the contract
narrow; revisit if a project forces the question.
"""
from verifier import runner, schema, types
from verifier.runner import VerifierResult, verify_card
from verifier.schema import SchemaError, validate_ac_items
from verifier.types import CANONICAL_TYPES, ACType

__all__ = [
    "ACType",
    "CANONICAL_TYPES",
    "SchemaError",
    "VerifierResult",
    "runner",
    "schema",
    "types",
    "validate_ac_items",
    "verify_card",
]
