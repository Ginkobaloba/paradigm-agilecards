"""Canonical AC type registry for the v1.3 verifier.

The single source of truth for which `type:` strings the verifier
accepts. Both the planner-side schema validator and the verifier-side
runner import from here, which keeps them in sync without an
out-of-band human-managed list.

Adding a new type:

1. Append it to `ACType` and `CANONICAL_TYPES`.
2. Add the matching handler under `verifier/handlers/` and register it
   in `verifier.runner.HANDLER_REGISTRY`.
3. Update the schema validator in `verifier.schema` with the
   per-type field requirements.
4. Update `RUNNER_CONTRACT.md` "Cold-read verification" and the
   `templates/card.md` AC documentation.

Removing a type is a breaking change and goes through the same
deprecation path as any schema field removal: alias-with-warning for
one minor version, then hard-fail.
"""
from __future__ import annotations

from enum import Enum
from typing import Final


class ACType(str, Enum):
    """Enumeration of every AC item type the v1.3 verifier recognizes.

    Backed by `str` so the value compares cleanly against the raw
    YAML payload. Membership tests on string input go through
    `ACType.from_str` which surfaces a clear error rather than the
    default `ValueError` from `Enum(...)`.
    """

    # Filesystem family.
    FILE_EXISTS = "file_exists"
    FILE_ABSENT = "file_absent"
    FILE_CONTAINS = "file_contains"
    FILE_ABSENT_CONTENT = "file_absent_content"

    # Process family.
    COMMAND = "command"
    PYTHON_ASSERT = "python_assert"

    # Network family. Gated by `network_checks_allowed` in project config.
    HTTP_STATUS = "http_status"
    HTTP_CONTAINS = "http_contains"

    # Human-judgment family. Cascaded LLM evaluation.
    SUBJECTIVE = "subjective"

    @classmethod
    def from_str(cls, raw: str) -> "ACType":
        try:
            return cls(raw)
        except ValueError as exc:
            valid = ", ".join(t.value for t in cls)
            raise ValueError(
                f"Unknown AC type {raw!r}. Valid types: {valid}."
            ) from exc


# Convenience exports. The list form is what external tooling
# (planner schema, dashboard hints, docs generator) usually wants;
# the set form is what membership checks should use for O(1).
CANONICAL_TYPES: Final[list[str]] = [t.value for t in ACType]
CANONICAL_TYPES_SET: Final[frozenset[str]] = frozenset(CANONICAL_TYPES)

# Family groupings. Used by the runner to gate network types behind
# project_config.network_checks_allowed and by the schema validator
# to give better error messages.
FILESYSTEM_TYPES: Final[frozenset[str]] = frozenset(
    {
        ACType.FILE_EXISTS.value,
        ACType.FILE_ABSENT.value,
        ACType.FILE_CONTAINS.value,
        ACType.FILE_ABSENT_CONTENT.value,
    }
)
PROCESS_TYPES: Final[frozenset[str]] = frozenset(
    {ACType.COMMAND.value, ACType.PYTHON_ASSERT.value}
)
NETWORK_TYPES: Final[frozenset[str]] = frozenset(
    {ACType.HTTP_STATUS.value, ACType.HTTP_CONTAINS.value}
)
SUBJECTIVE_TYPES: Final[frozenset[str]] = frozenset(
    {ACType.SUBJECTIVE.value}
)
DETERMINISTIC_TYPES: Final[frozenset[str]] = (
    FILESYSTEM_TYPES | PROCESS_TYPES | NETWORK_TYPES
)


# Default timeouts in seconds. Per-item `timeout_sec` overrides; the
# project config may also override globally. Filesystem types have
# no timeout because the underlying syscall is synchronous and either
# returns or the kernel hangs (in which case a verifier timeout is
# the least of our problems).
DEFAULT_TIMEOUTS_SEC: Final[dict[str, int | None]] = {
    ACType.FILE_EXISTS.value: None,
    ACType.FILE_ABSENT.value: None,
    ACType.FILE_CONTAINS.value: None,
    ACType.FILE_ABSENT_CONTENT.value: None,
    ACType.COMMAND.value: 60,
    ACType.PYTHON_ASSERT.value: 5,
    ACType.HTTP_STATUS.value: 10,
    ACType.HTTP_CONTAINS.value: 10,
    ACType.SUBJECTIVE.value: 60,
}


# Default retry policy for HTTP-family checks. Two retries with
# exponential backoff at 1s, 2s. Per-item override allowed.
HTTP_DEFAULT_RETRIES: Final[int] = 2
HTTP_DEFAULT_BACKOFF_SEC: Final[tuple[float, ...]] = (1.0, 2.0)
