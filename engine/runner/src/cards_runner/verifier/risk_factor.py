"""Structured risk factors emitted by the verifier (gate chunk 1).

`docs/design/confidence_driven_merge_gate.md` section 3.6: the v1.3.1
verifier enumerates code-level risks it noticed in the diff or evidence,
each tagged low / medium / high. The confidence gate (a later chunk)
consumes the *severity* to decide who reviews a merge; high is a hard
escalator, medium/low are soft-signal deductions. The `kind` enum is
extensible -- the gate keys off severity, not kind, so adding a kind
never requires re-tuning weights.

This chunk (gate-1) only defines the schema and plumbs it through the
verifier result. No gate consumes it yet; the field rides along the
existing cascade flow as a no-op until the gate skeleton lands.

Design decision (spec 3.6): severity is the verifier's call, not the
gate's. The verifier sees the code; it classifies "new HTTP call to a
known-internal host" vs "to a brand-new external host". The gate stays
ignorant of code semantics and just reads the tag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Severity tags. The carrier the gate reads.
SEVERITY_LOW: str = "low"
SEVERITY_MEDIUM: str = "medium"
SEVERITY_HIGH: str = "high"
SEVERITIES: tuple[str, ...] = (SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH)

# Known kinds (spec 3.6). Extensible: an unrecognized kind is preserved
# verbatim so a newer verifier prompt can emit kinds the gate hasn't been
# taught yet without losing data.
KIND_EXTERNAL_CALL_ADDED: str = "external_call_added"
KIND_GUARD_REMOVED: str = "guard_removed"
KIND_RAW_SQL: str = "raw_sql"
KIND_STRING_EVAL: str = "string_eval"
KIND_CRYPTO_CHANGE: str = "crypto_change"
KIND_ERROR_SWALLOWED: str = "error_swallowed"
KIND_CONCURRENCY_CHANGE: str = "concurrency_change"
KIND_PERMISSION_CHANGE: str = "permission_change"
KIND_UNVERIFIED_ASSUMPTION: str = "unverified_assumption"
KIND_INCOMPLETE_TEST_COVERAGE: str = "incomplete_test_coverage"
KIND_DEP_PIN_LOOSENED: str = "dep_pin_loosened"

KNOWN_KINDS: tuple[str, ...] = (
    KIND_EXTERNAL_CALL_ADDED,
    KIND_GUARD_REMOVED,
    KIND_RAW_SQL,
    KIND_STRING_EVAL,
    KIND_CRYPTO_CHANGE,
    KIND_ERROR_SWALLOWED,
    KIND_CONCURRENCY_CHANGE,
    KIND_PERMISSION_CHANGE,
    KIND_UNVERIFIED_ASSUMPTION,
    KIND_INCOMPLETE_TEST_COVERAGE,
    KIND_DEP_PIN_LOOSENED,
)


@dataclass(frozen=True)
class RiskFactor:
    """One code-level risk the verifier observed. Spec section 3.6."""

    kind: str
    severity: str  # one of SEVERITIES
    description: str
    location: str | None = None
    source_item_idx: int | None = None

    def is_known_kind(self) -> bool:
        return self.kind in KNOWN_KINDS


def _coerce_severity(value: Any) -> str:
    """Map a raw severity to a canonical tag.

    An unknown or missing severity defaults to `low` -- the least
    escalation -- so a sloppy model emission never silently inflates a
    risk into a hard escalator. The verifier is asked to tag explicitly;
    this is the safety net, not the happy path."""
    text = str(value or "").strip().lower()
    return text if text in SEVERITIES else SEVERITY_LOW


def parse_risk_factors(raw: Any) -> tuple[RiskFactor, ...]:
    """Build a tuple of `RiskFactor` from a model's `risk_factors` list.

    Total and forgiving: a non-list returns empty; a malformed entry
    (not a dict, or missing a kind) is skipped rather than raising. The
    verifier result must never fail to assemble because the model emitted
    a stray risk-factor entry."""
    if not isinstance(raw, list):
        return ()
    out: list[RiskFactor] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip()
        if not kind:
            continue
        idx_raw = entry.get("source_item_idx")
        try:
            source_idx = None if idx_raw is None else int(idx_raw)
        except (TypeError, ValueError):
            source_idx = None
        location = entry.get("location")
        out.append(RiskFactor(
            kind=kind,
            severity=_coerce_severity(entry.get("severity")),
            description=str(entry.get("description") or ""),
            location=None if location is None else str(location),
            source_item_idx=source_idx,
        ))
    return tuple(out)


__all__ = [
    "KIND_CONCURRENCY_CHANGE",
    "KIND_CRYPTO_CHANGE",
    "KIND_DEP_PIN_LOOSENED",
    "KIND_ERROR_SWALLOWED",
    "KIND_EXTERNAL_CALL_ADDED",
    "KIND_GUARD_REMOVED",
    "KIND_INCOMPLETE_TEST_COVERAGE",
    "KIND_PERMISSION_CHANGE",
    "KIND_RAW_SQL",
    "KIND_STRING_EVAL",
    "KIND_UNVERIFIED_ASSUMPTION",
    "KNOWN_KINDS",
    "SEVERITIES",
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "RiskFactor",
    "parse_risk_factors",
]
