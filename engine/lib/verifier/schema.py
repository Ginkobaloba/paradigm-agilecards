"""Schema validation for v1.3 acceptance criteria.

Imported by:

- The planner, at card write time, to refuse a batch where any card
  declares an invalid AC item.
- The verifier, at run time, to surface schema problems on cards that
  may have been edited by hand between planning and execution
  (defense in depth, per locked answer 8 in the design doc).

The validator returns structured errors keyed by card id and item
index. The planner surfaces them in its abort message; the runner
moves the card to `blocked/` with the structured error attached to
completion notes.

This module deliberately does NOT validate the value of individual
fields beyond shape (e.g., it does not check that a `path` actually
exists). Field-level execution happens in the handler; the schema
layer only verifies that the items the planner emitted are
well-formed enough that a handler can be dispatched against them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verifier.types import (
    CANONICAL_TYPES,
    CANONICAL_TYPES_SET,
    NETWORK_TYPES,
    SUBJECTIVE_TYPES,
    ACType,
)


class SchemaError(Exception):
    """Raised when an AC item or a list of AC items fails validation.

    Carries `card_id`, `item_idx`, and a human-readable `message`. The
    planner formats these for its abort summary; the runner stuffs
    them into completion notes verbatim.
    """

    def __init__(
        self,
        message: str,
        *,
        card_id: str | None = None,
        item_idx: int | None = None,
    ) -> None:
        self.card_id = card_id
        self.item_idx = item_idx
        self.message = message
        super().__init__(self._format())

    def _format(self) -> str:
        prefix = ""
        if self.card_id is not None:
            prefix += f"[card={self.card_id}]"
        if self.item_idx is not None:
            prefix += f"[item={self.item_idx}]"
        if prefix:
            return f"{prefix} {self.message}"
        return self.message


@dataclass(frozen=True)
class ValidationIssue:
    """One schema violation, suitable for batch reporting.

    The schema validator can collect every issue across a card (or
    across a batch of cards) instead of fast-failing on the first.
    The planner uses this so users see every broken item in one pass
    rather than fixing them one round-trip at a time.
    """

    card_id: str | None
    item_idx: int | None
    message: str


@dataclass(frozen=True)
class ValidationReport:
    """Aggregated result of validating a card or batch.

    `ok` is true if and only if `issues` is empty. The runner asserts
    on `ok`; the planner formats `issues` for the user.
    """

    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.issues


# Per-type required and optional field maps. The keys are the
# canonical type values from `verifier.types.ACType`.
_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    ACType.FILE_EXISTS.value: frozenset({"path"}),
    ACType.FILE_ABSENT.value: frozenset({"path"}),
    ACType.FILE_CONTAINS.value: frozenset({"path"}),  # plus xor(pattern, literal)
    ACType.FILE_ABSENT_CONTENT.value: frozenset({"path"}),  # plus xor(pattern, literal)
    ACType.COMMAND.value: frozenset({"command"}),
    ACType.PYTHON_ASSERT.value: frozenset({"expression"}),
    ACType.HTTP_STATUS.value: frozenset({"url", "expected_status"}),
    ACType.HTTP_CONTAINS.value: frozenset({"url"}),  # plus xor(pattern, literal)
    ACType.SUBJECTIVE.value: frozenset({"description", "evidence_required"}),
}

_OPTIONAL_FIELDS: dict[str, frozenset[str]] = {
    ACType.FILE_EXISTS.value: frozenset(),
    ACType.FILE_ABSENT.value: frozenset(),
    ACType.FILE_CONTAINS.value: frozenset({"pattern", "literal", "case_sensitive"}),
    ACType.FILE_ABSENT_CONTENT.value: frozenset(
        {"pattern", "literal", "case_sensitive"}
    ),
    ACType.COMMAND.value: frozenset(
        {"expected_exit_code", "cwd", "env", "timeout_sec"}
    ),
    ACType.PYTHON_ASSERT.value: frozenset({"timeout_sec"}),
    ACType.HTTP_STATUS.value: frozenset(
        {"method", "headers", "body", "timeout_sec", "retries"}
    ),
    ACType.HTTP_CONTAINS.value: frozenset(
        {
            "expected_status",
            "method",
            "headers",
            "body",
            "timeout_sec",
            "retries",
            "pattern",
            "literal",
            "case_sensitive",
        }
    ),
    ACType.SUBJECTIVE.value: frozenset(),
}

# Common fields permitted on every item regardless of type.
_COMMON_FIELDS: frozenset[str] = frozenset({"description", "type"})

# Types that XOR pattern with literal. The two are mutually exclusive
# but exactly one is required.
_PATTERN_XOR_TYPES: frozenset[str] = frozenset(
    {
        ACType.FILE_CONTAINS.value,
        ACType.FILE_ABSENT_CONTENT.value,
        ACType.HTTP_CONTAINS.value,
    }
)


def _validate_item(
    item: Any,
    *,
    card_id: str | None,
    item_idx: int,
    card_points: int | None,
    network_checks_allowed: bool,
) -> list[ValidationIssue]:
    """Validate one AC item. Returns a list of issues (empty = clean)."""
    issues: list[ValidationIssue] = []

    def _add(msg: str) -> None:
        issues.append(
            ValidationIssue(card_id=card_id, item_idx=item_idx, message=msg)
        )

    if not isinstance(item, dict):
        _add(
            f"AC item must be a mapping, got {type(item).__name__}. "
            f"Expected shape: {{description, type, ...type-specific fields}}."
        )
        return issues

    if "description" not in item:
        _add("AC item is missing required field 'description'.")
    elif not isinstance(item["description"], str) or not item["description"].strip():
        _add("AC item 'description' must be a non-empty string.")

    if "type" not in item:
        _add(
            "AC item is missing required field 'type'. "
            f"Valid types: {', '.join(CANONICAL_TYPES)}."
        )
        return issues

    raw_type = item["type"]
    if not isinstance(raw_type, str) or raw_type not in CANONICAL_TYPES_SET:
        _add(
            f"AC item 'type' is {raw_type!r}, which is not a recognized "
            f"canonical type. Valid types: {', '.join(CANONICAL_TYPES)}."
        )
        return issues

    # Network gating.
    if raw_type in NETWORK_TYPES and not network_checks_allowed:
        _add(
            f"AC item declares network type {raw_type!r} but the project "
            f"config does not set network_checks_allowed: true. Network "
            f"checks are disabled by default."
        )

    # Subjective tier-gating: planner-side rule. The original v1.1
    # constraint that only tier 5 / 6 cards may carry subjective items
    # is preserved in v1.3.
    if raw_type in SUBJECTIVE_TYPES and card_points is not None and card_points < 5:
        _add(
            f"AC item declares 'subjective' but card points={card_points}. "
            f"Subjective items are only permitted on tier 5 / 6 cards. "
            f"Either resize the card or convert this AC to a deterministic "
            f"type."
        )

    required = _REQUIRED_FIELDS[raw_type]
    optional = _OPTIONAL_FIELDS[raw_type]
    allowed = required | optional | _COMMON_FIELDS

    for missing in required - set(item.keys()):
        _add(
            f"AC item of type {raw_type!r} is missing required field "
            f"{missing!r}."
        )

    for unknown in set(item.keys()) - allowed:
        _add(
            f"AC item of type {raw_type!r} carries unknown field "
            f"{unknown!r}. Allowed fields: "
            f"{', '.join(sorted(allowed))}."
        )

    if raw_type in _PATTERN_XOR_TYPES:
        has_pattern = "pattern" in item
        has_literal = "literal" in item
        if has_pattern == has_literal:
            _add(
                f"AC item of type {raw_type!r} must declare exactly one of "
                f"'pattern' or 'literal' (got "
                f"{'both' if has_pattern else 'neither'})."
            )

    # Per-type shallow value checks. Deep value checks (e.g., path is
    # actually a valid OS path, URL is parseable) live in the handler.
    if raw_type == ACType.COMMAND.value and "command" in item:
        cmd = item["command"]
        if not isinstance(cmd, (str, list)):
            _add(
                f"AC item of type 'command' has 'command' field of "
                f"type {type(cmd).__name__}; expected str or list[str]."
            )

    if raw_type == ACType.HTTP_STATUS.value and "expected_status" in item:
        es = item["expected_status"]
        ok = isinstance(es, int) or (
            isinstance(es, list)
            and all(isinstance(x, int) for x in es)
            and len(es) > 0
        )
        if not ok:
            _add(
                "AC item of type 'http_status' has 'expected_status' field "
                "that is neither int nor non-empty list[int]."
            )

    if "timeout_sec" in item and not (
        isinstance(item["timeout_sec"], int) and item["timeout_sec"] > 0
    ):
        _add("AC item 'timeout_sec' must be a positive integer.")

    if "retries" in item and not (
        isinstance(item["retries"], int) and item["retries"] >= 0
    ):
        _add("AC item 'retries' must be a non-negative integer.")

    return issues


def validate_ac_items(
    items: list[Any],
    *,
    card_id: str | None = None,
    card_points: int | None = None,
    network_checks_allowed: bool = False,
) -> ValidationReport:
    """Validate a list of AC items as written in a card frontmatter.

    Parameters:
        items: the raw list parsed from the YAML block.
        card_id: optional; the card id, used in error reporting.
        card_points: optional; if set, the validator enforces the
            tier 5 / 6 rule for subjective items.
        network_checks_allowed: whether the project config has opted
            into network types.

    Returns a `ValidationReport`. The report is empty iff every item
    passed validation.
    """
    issues: list[ValidationIssue] = []

    if not isinstance(items, list):
        issues.append(
            ValidationIssue(
                card_id=card_id,
                item_idx=None,
                message=(
                    f"acceptance_criteria must be a list, got "
                    f"{type(items).__name__}."
                ),
            )
        )
        return ValidationReport(issues=tuple(issues))

    if len(items) == 0:
        issues.append(
            ValidationIssue(
                card_id=card_id,
                item_idx=None,
                message=(
                    "acceptance_criteria is empty. Every card must declare "
                    "at least one AC item (otherwise the card has no "
                    "definition of done)."
                ),
            )
        )
        return ValidationReport(issues=tuple(issues))

    for idx, item in enumerate(items):
        issues.extend(
            _validate_item(
                item,
                card_id=card_id,
                item_idx=idx,
                card_points=card_points,
                network_checks_allowed=network_checks_allowed,
            )
        )

    return ValidationReport(issues=tuple(issues))


def raise_on_invalid(
    items: list[Any],
    *,
    card_id: str | None = None,
    card_points: int | None = None,
    network_checks_allowed: bool = False,
) -> None:
    """Convenience wrapper that raises `SchemaError` on the first issue.

    The planner uses `validate_ac_items` to collect every issue.
    Internal call sites that want fast-fail use this. The first
    issue's `card_id` and `item_idx` are preserved on the exception.
    """
    report = validate_ac_items(
        items,
        card_id=card_id,
        card_points=card_points,
        network_checks_allowed=network_checks_allowed,
    )
    if not report.ok:
        first = report.issues[0]
        raise SchemaError(
            first.message,
            card_id=first.card_id,
            item_idx=first.item_idx,
        )
