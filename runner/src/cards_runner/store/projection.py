"""Card `.md` <-> `CardRecord` conversion.

This module is the compatibility hinge of the whole storage move. It
does two jobs:

1. Parse a v1 card file into a `CardRecord` (the migration import
   path, and any future ingest path).
2. Project a `CardRecord` back into a card `.md` file (the per-run
   worktree projection, and the migration verifier's witness).

The byte-faithful guarantee, stated precisely: a card read with
`card_file_to_record` and written back with
`render_card_text(record, verbatim=True)` reproduces the source file
exactly, because the frontmatter text and the body are stored
verbatim and reassembled with the same `---` fences. That is what
makes the v1 migration provably lossless.

`render_card_text(record, verbatim=False)` is the other mode: it
rebuilds the frontmatter from the live column values, so a card
projected after the runner has updated it shows the current state.
That projection is well-formed and contract-faithful but not
byte-identical to anything, which is correct: once the database is
canonical the card file is an ephemeral per-run view, not a tracked
artifact anyone diffs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ..common.types import CANONICAL_WORK_TYPES, is_canonical_work_type
from .models import (
    DEFAULT_TENANT,
    FIELD_VALUE_OVERRIDES,
    INTEGER_FIELD_NAMES,
    CardRecord,
    CardStatus,
)

# Frontmatter fenced by two `---` lines, body is everything after.
# Kept local so this module does not depend on card_io internals
# (card_io's writer is superseded by the repository in chunk 2b; its
# parser regex is small enough to own).
#
# The fence whitespace class is `[^\S\r\n]` (spaces and tabs, not
# newlines) on purpose. A plain `\s*` is greedy and eats the blank
# line that often follows the closing fence, which silently shifts a
# byte out of the body and breaks the verbatim round trip. Only the
# single explicit `\n` terminates each fence line.
_FRONTMATTER_RE = re.compile(
    r"^---[^\S\r\n]*\n(?P<fm>.*?)\n---[^\S\r\n]*\n(?P<body>.*)\Z",
    re.DOTALL,
)

# yaml.safe_load resolves bare ISO date and timestamp scalars into
# Python date/datetime objects. Those are not JSON-serializable and
# would break the frontmatter_extra column, and they also lose the
# original string form. v1 cards carry dates and timestamps as plain
# text, so the timestamp implicit resolver is stripped from the loader
# and every scalar stays a string.
class _CardYamlLoader(yaml.SafeLoader):
    pass


_CardYamlLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers
          if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

# Reverse of models.FIELD_VALUE_OVERRIDES: a frontmatter `status:` of
# `awaiting_amendment_review` normalizes to the canonical `amendments`
# status stored in the column.
_STATUS_FIELD_TO_CANONICAL: dict[str, str] = {
    v: k for k, v in FIELD_VALUE_OVERRIDES.items()
}

# Canonical frontmatter key order for the rebuilt (non-verbatim)
# projection. Matches templates/card.md so a projected card reads the
# way a planned card reads. Unknown keys are appended in sorted order.
_CANONICAL_FIELD_ORDER: tuple[str, ...] = (
    "verifier_schema_version",
    "id",
    "title",
    "project",
    "status",
    "points",
    "stakes",
    "difficulty",
    "work_type",
    "thinking_depth",
    "model",
    "extended_thinking",
    "model_floor",
    "pin_required",
    "requires_pre_approval",
    "cost_cap_usd",
    "estimated_tokens",
    "actual_tokens",
    "estimated_duration_minutes",
    "actual_duration_minutes",
    "trace_id",
    "sizing_note",
    "depends_on",
    "touches",
    "batch",
    "story_hash",
    "created",
    "started_at",
    "finished_at",
    "claimed_by",
    "model_used",
    "attempt_trace_id",
    "last_heartbeat",
    "branch",
    "base_branch",
    "merge_status",
    "pr_url",
    "verified_at",
    "verified_by",
    "verifier_skipped_reason",
    "cascade_history",
    "verifier_cascade_history",
    "standup_reason",
)


class ProjectionError(Exception):
    """Raised when a card file cannot be parsed into a record."""


@dataclass(frozen=True)
class ParsedCard:
    """The raw split of a card file: frontmatter dict, body, raw text."""

    frontmatter: dict[str, Any]
    body_md: str
    frontmatter_raw: str


def parse_card_text(text: str) -> ParsedCard:
    """Split card `.md` text into frontmatter dict, body, and raw text.

    The raw frontmatter text is preserved so the verbatim projection
    can reproduce the original byte-for-byte.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ProjectionError(
            "card text missing YAML frontmatter fenced by '---' lines"
        )
    fm_raw = match.group("fm")
    body = match.group("body")
    loaded: Any = yaml.load(fm_raw, Loader=_CardYamlLoader) or {}
    if not isinstance(loaded, dict):
        raise ProjectionError(
            f"frontmatter parsed to {type(loaded).__name__}, expected mapping"
        )
    return ParsedCard(frontmatter=loaded, body_md=body, frontmatter_raw=fm_raw)


def card_text_to_record(
    text: str,
    *,
    tenant_id: str = DEFAULT_TENANT,
    card_id_fallback: str | None = None,
    status_override: str | None = None,
) -> CardRecord:
    """Build a `CardRecord` from card `.md` text.

    `card_id_fallback` supplies the id when the frontmatter has no
    `id:` (matches card_io's filename-stem fallback). `status_override`
    lets the migration tool make the subfolder canonical, since
    RUNNER_CONTRACT.md says the subfolder wins over the `status:`
    field when they disagree.
    """
    parsed = parse_card_text(text)
    return _record_from_parsed(
        parsed,
        tenant_id=tenant_id,
        card_id_fallback=card_id_fallback,
        status_override=status_override,
    )


def card_file_to_record(
    path: Path,
    *,
    tenant_id: str = DEFAULT_TENANT,
    status_override: str | None = None,
) -> CardRecord:
    """Read and parse a card file into a `CardRecord`."""
    text = path.read_text(encoding="utf-8")
    return card_text_to_record(
        text,
        tenant_id=tenant_id,
        card_id_fallback=path.stem,
        status_override=status_override,
    )


def _record_from_parsed(
    parsed: ParsedCard,
    *,
    tenant_id: str,
    card_id_fallback: str | None,
    status_override: str | None,
) -> CardRecord:
    fm = parsed.frontmatter
    card_id = str(fm.get("id") or card_id_fallback or "")
    if not card_id:
        raise ProjectionError("card has no id and no fallback was given")

    # Per docs/design/throughput_metrics_ledger.md section 4.1, the
    # runner validates work_type on projection and rejects an unknown
    # enum value with a clear error. A missing field (None) is the
    # legacy-backfill case and passes through untouched; chunk 2's
    # writer stamps it `unknown` with incomplete_metrics=True. A value
    # that is present but not canonical is a planner typo that would
    # silently fragment every estimator / contract-survival / trust
    # bucket keyed off this field, so it fails loudly here.
    raw_work_type = fm.get("work_type")
    if raw_work_type is not None and not is_canonical_work_type(
        str(raw_work_type)
    ):
        raise ProjectionError(
            f"card {card_id!r} has work_type {raw_work_type!r}, which is not "
            f"a canonical value. Allowed: {', '.join(CANONICAL_WORK_TYPES)}. "
            "New cards must use a first-class type; 'unknown' is reserved for "
            "pre-ledger backfill."
        )

    if status_override is not None:
        status = status_override
    else:
        raw_status = str(fm.get("status") or CardStatus.BACKLOG.value)
        status = _STATUS_FIELD_TO_CANONICAL.get(raw_status, raw_status)

    record = CardRecord(
        card_id=card_id,
        tenant_id=tenant_id,
        status=status,
        frontmatter_raw=parsed.frontmatter_raw,
        body_md=parsed.body_md,
    )

    # Promote the hot fields onto typed attributes; everything else
    # lands in the extra dict so nothing is dropped.
    extra: dict[str, Any] = {}
    for key, value in fm.items():
        if key in ("id", "status"):
            continue
        if key in _PROMOTABLE_ATTRS:
            setattr(record, key, _coerce(key, value))
        else:
            extra[key] = value
    record.frontmatter_extra = extra
    return record


# Attributes on CardRecord that a frontmatter key may be promoted to.
# Excludes the structural fields (frontmatter_raw, body_md, ...).
_PROMOTABLE_ATTRS: frozenset[str] = frozenset({
    "title",
    "project",
    "batch",
    "points",
    "stakes",
    "difficulty",
    "claimed_by",
    "attempt_trace_id",
    "model_used",
    "created",
    "started_at",
    "finished_at",
    "last_heartbeat",
    "merge_status",
    "verified_at",
    "verified_by",
    "estimated_tokens",
    "actual_tokens",
    "story_hash",
    "trace_id",
    "pr_url",
    "work_type",
})


def _coerce(key: str, value: Any) -> Any:
    """Coerce a promoted field to its stored type. None passes through."""
    if value is None:
        return None
    if key in INTEGER_FIELD_NAMES:
        return int(value)
    if key == "created":
        # `created` is a date in v1 cards; store the ISO string form.
        return str(value)
    return value


def record_to_frontmatter(record: CardRecord) -> dict[str, Any]:
    """Reconstruct the full frontmatter dict from a record's live state.

    Promoted columns plus the `frontmatter_extra` tail, with `id` and
    the contract's `status:` field-value override applied. This is the
    dict the non-verbatim projection serializes.
    """
    fm: dict[str, Any] = {"id": record.card_id}
    for attr in _PROMOTABLE_ATTRS:
        value = getattr(record, attr)
        if value is not None:
            fm[attr] = value
    fm["status"] = FIELD_VALUE_OVERRIDES.get(record.status, record.status)
    fm.update(record.frontmatter_extra)
    return fm


def render_card_text(record: CardRecord, *, verbatim: bool = False) -> str:
    """Project a `CardRecord` back to card `.md` text.

    `verbatim=True` reassembles the immutable capture columns and is
    byte-faithful to the source file. `verbatim=False` rebuilds the
    frontmatter from the record's live field values in canonical key
    order, reflecting any updates the runner has made.
    """
    if verbatim:
        return f"---\n{record.frontmatter_raw}\n---\n{record.body_md}"
    fm = record_to_frontmatter(record)
    ordered = _order_frontmatter(fm)
    fm_text = yaml.safe_dump(
        ordered,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).rstrip("\n")
    return f"---\n{fm_text}\n---\n{record.body_md}"


def _order_frontmatter(fm: dict[str, Any]) -> dict[str, Any]:
    """Return `fm` reordered by the canonical key order.

    Keys in `_CANONICAL_FIELD_ORDER` come first in that order; any
    remaining keys follow, sorted, so the output is deterministic.
    """
    ordered: dict[str, Any] = {}
    for key in _CANONICAL_FIELD_ORDER:
        if key in fm:
            ordered[key] = fm[key]
    for key in sorted(fm):
        if key not in ordered:
            ordered[key] = fm[key]
    return ordered


def project_card_file(
    record: CardRecord, path: Path, *, verbatim: bool = False
) -> None:
    """Write a card's projected `.md` to `path`.

    Chunk 2b uses this to drop the per-run card file into the
    executor's worktree. Newlines are written through unchanged (no
    platform translation) so the projection is stable across hosts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_card_text(record, verbatim=verbatim),
        encoding="utf-8",
        newline="",
    )
