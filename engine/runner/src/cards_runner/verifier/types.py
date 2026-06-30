"""Canonical acceptance-criterion types and the deprecated-alias table.

RUNNER_CONTRACT.md "Library requirement" makes this the single source
of truth: planner-side AC machinability checks and runner-side
dispatch both read from the same registry, so drift between them is a
Python import error rather than a silent runtime mismatch. The
canonical list ships with the runner; the planner-side library
(`lib/verifier/types.py` in the /cards skill) is the same names.

Each type names a handler the runner provides. A type the runner does
NOT implement is a `SchemaError` -- the contract is firm that a card
declaring such a type lands in `blocked/` with the schema-validation
error in completion notes, rather than being silently dropped.
"""
from __future__ import annotations

from typing import Final


# Canonical type names, exactly as a card's acceptance_criteria block
# is expected to spell them. Order is documentation order; the lookup
# is the set, not the sequence.
CANONICAL_TYPES: Final[frozenset[str]] = frozenset({
    "file_exists",
    "file_absent",
    "file_contains",
    "file_lacks",
    "shell",
    "subjective",
})


# Deprecated v1.2 type names accepted by the v1.3 runner with a
# deprecation warning. Maps the legacy name to the canonical name.
# RUNNER_CONTRACT.md: "the legacy type names (`shell`, `grep_match`,
# `grep_absent`) are accepted by the runner via the alias table".
# `shell` is the v1.3 canonical name and is intentionally identity-
# mapped here so a card carrying it round-trips unchanged.
LEGACY_TYPE_ALIASES: Final[dict[str, str]] = {
    "grep_match": "file_contains",
    "grep_absent": "file_lacks",
    "shell": "shell",
}


class SchemaError(Exception):
    """A card declared an AC type the runner does not implement, or an
    AC item is missing a required field.

    Per RUNNER_CONTRACT.md the runner does not attempt to silently
    coerce or skip such items; the verifier surfaces the schema error
    to the caller (`verify_card`) and the daemon routes the card to
    `blocked/` with the message in `verifier_notes`.
    """


def canonicalize_type(declared: str) -> tuple[str, bool]:
    """Return (canonical_type, used_alias) or raise `SchemaError`.

    A declared name that is already canonical returns
    `(declared, False)`. A legacy alias returns the canonical name
    and `True` so the caller can emit one deprecation warning per
    card. Anything else is a `SchemaError`.
    """
    name = (declared or "").strip()
    if not name:
        raise SchemaError("acceptance-criterion item is missing `type:`")
    if name in CANONICAL_TYPES:
        return name, False
    if name in LEGACY_TYPE_ALIASES:
        return LEGACY_TYPE_ALIASES[name], True
    raise SchemaError(
        f"unknown acceptance-criterion type {name!r}; canonical types "
        f"are {sorted(CANONICAL_TYPES)}; deprecated aliases are "
        f"{sorted(LEGACY_TYPE_ALIASES)}"
    )
