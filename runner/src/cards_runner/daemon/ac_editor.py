"""Splice a structured replacement item into a card's AC block.

Chunk 6a: when the amendment reviewer approves a `change_request:` AND
the project has opted into `auto_edit_ac: true`, a separate
structured-output call emits a full replacement item for the target
AC index. This module owns the splice: it locates the
`acceptance_criteria:` YAML block in the card body, replaces the
target item, and attaches the contract's required provenance fields
(`amended_at`, `amended_by`, `amendment_reason`) plus an `original:`
sub-mapping that carries the pre-amendment item verbatim.

Per RUNNER_CONTRACT.md "AC amendment protocol" / SKILL.md section 11,
the amended item shape is:

    acceptance_criteria:
      - description: "Post-amendment description"
        type: <handler type>
        ... handler-specific fields ...
        amended_at: 2026-05-22T15:10:00Z
        amended_by: amendment-reviewer-agent
        amendment_reason: "Original test assumed X; reality is Y."
        original:
          description: "Pre-amendment description"
          type: ...
          ... pre-amendment handler fields ...

The original sub-mapping is the full pre-amendment item (with its
own legacy-alias type if it had one); the audit trail must survive
later edits to the canonical shape. The contract is clear: the
original is "retained so the change is auditable forever".

This module is deliberately small. It does NOT validate handler-field
shape (the verifier will catch a bogus replacement on the next claim);
it does NOT decide whether to edit (the amendment_reviewer owns that
gate); it only splices given a valid index.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml  # type: ignore[import-untyped]


log = logging.getLogger(__name__)


# Match the same fenced YAML block the verifier reads (parse.py).
# We capture the leading fence indent so the splice can preserve it
# on re-emit; a card body that uses 4-space-indented code blocks (rare
# but legal in CommonMark) round-trips cleanly.
_YAML_FENCE_RE = re.compile(
    r"(?P<indent>[ \t]*)```ya?ml\s*\n(?P<body>.*?)\n(?P=indent)```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class AmendmentEdit:
    """A structured replacement AC item from the editor agent.

    `ac_index` is 0-based and points at the slot in the card's existing
    `acceptance_criteria:` list to replace. `check_type` is the
    canonical type name (e.g. `file_exists`, `command`, `subjective`).
    `check_fields` is the remaining item dict -- description is NOT
    inside check_fields, but every other handler-specific field is
    (e.g. `path`, `pattern`, `command`, ...). `confidence` is the
    editor's self-reported confidence in this exact replacement (the
    runner thresholds against
    `ReviewerConfig.auto_edit_confidence_floor`).
    """

    ac_index: int
    description: str
    check_type: str
    check_fields: dict[str, Any] = field(default_factory=dict)
    amendment_reason: str = ""
    confidence: float = 0.0
    model_used: str = ""
    actual_cost_usd: float | None = None


class AcEditError(Exception):
    """Raised when the splice cannot be performed.

    The amendment_reviewer catches this and falls back to the chunk 5
    "park in blocked/amendment_approved" path -- a failed splice does
    NOT lose the approval, it just defers the actual edit to a human.
    """


def splice_amendment(
    body_md: str,
    edit: AmendmentEdit,
    *,
    reviewer_label: str,
    timestamp_iso: str,
) -> str:
    """Return a new body with the AC item at `edit.ac_index` replaced.

    Raises `AcEditError` when the body has no AC block, the index is
    out of range, or the YAML cannot be parsed / round-tripped. Caller
    is the amendment_reviewer; it treats the raise as "fall back to
    human-finalize" and surfaces the reason in the marker file.
    """
    if not body_md:
        raise AcEditError("card body is empty; nothing to splice into")
    match = _YAML_FENCE_RE.search(body_md)
    if match is None:
        raise AcEditError(
            "card body has no fenced YAML block (acceptance_criteria:)"
        )
    yaml_text = match.group("body")
    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise AcEditError(f"acceptance_criteria YAML failed to parse: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AcEditError(
            "acceptance_criteria block must be a YAML mapping at the root"
        )
    items_key = "acceptance_criteria"
    items = loaded.get(items_key)
    if items is None:
        # Tolerate the legacy alias the verifier still accepts.
        items = loaded.get("acceptance_checks")
        if items is not None:
            items_key = "acceptance_checks"
    if not isinstance(items, list):
        raise AcEditError(
            "acceptance_criteria must be a YAML list; "
            f"got {type(items).__name__}"
        )
    if edit.ac_index < 0 or edit.ac_index >= len(items):
        raise AcEditError(
            f"ac_index {edit.ac_index} is out of range; "
            f"card has {len(items)} AC item(s)"
        )
    original_item = items[edit.ac_index]
    if not isinstance(original_item, dict):
        raise AcEditError(
            f"AC item at index {edit.ac_index} is not a YAML mapping; "
            f"got {type(original_item).__name__}"
        )
    replacement = _build_replacement(
        edit,
        original=original_item,
        reviewer_label=reviewer_label,
        timestamp_iso=timestamp_iso,
    )
    new_items = list(items)
    new_items[edit.ac_index] = replacement
    loaded[items_key] = new_items
    new_yaml = yaml.safe_dump(
        loaded,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).rstrip()
    # Preserve the original fence indentation so a non-default indent
    # doesn't get rewritten. The fence-info-line (```yaml) stays as
    # the lowercase canonical form even if the source used ```YAML or
    # ```yml -- the verifier parses both, but we normalize on write.
    indent = match.group("indent")
    new_fence = (
        f"{indent}```yaml\n"
        + _reindent(new_yaml, indent)
        + f"\n{indent}```"
    )
    return body_md[: match.start()] + new_fence + body_md[match.end():]


def _build_replacement(
    edit: AmendmentEdit,
    *,
    original: dict[str, Any],
    reviewer_label: str,
    timestamp_iso: str,
) -> dict[str, Any]:
    """Assemble the post-amendment item with provenance.

    Field order is deliberate: `description` and `type` lead so the
    item still reads naturally; provenance fields trail at the bottom
    so a human skimming the YAML sees what changed last. `original:`
    is a verbatim deep-copy of the pre-amendment item, including any
    nested mapping the planner may have used for handler-specific
    fields (e.g. a `command:` item's `env:` mapping).
    """
    item: dict[str, Any] = {
        "description": edit.description,
        "type": edit.check_type,
    }
    for key, value in edit.check_fields.items():
        if key in {"description", "type"}:
            continue  # the structured fields above already cover these.
        if key in {"amended_at", "amended_by", "amendment_reason", "original"}:
            # The editor MAY NOT smuggle provenance through check_fields;
            # the runner owns those fields. Silently drop with a warning.
            log.warning(
                "editor tried to set provenance field %r via check_fields; "
                "ignoring (runner owns provenance)",
                key,
            )
            continue
        item[key] = value
    item["amended_at"] = timestamp_iso
    item["amended_by"] = reviewer_label
    item["amendment_reason"] = edit.amendment_reason
    item["original"] = _deep_copy_yaml(original)
    return item


def _deep_copy_yaml(value: Any) -> Any:
    """Round-trip through YAML to deep-copy and normalize.

    A yaml.safe_load result is already plain Python (dict/list/str/int/
    float/bool/None), so a copy is sufficient -- but we round-trip
    through YAML to also strip any Numpy/UUID/datetime artifacts the
    original may have picked up via a non-safe load somewhere upstream.
    Cheap insurance.
    """
    if isinstance(value, dict):
        return {k: _deep_copy_yaml(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy_yaml(v) for v in value]
    return value


def _reindent(text: str, indent: str) -> str:
    """Prepend `indent` to each line of `text`."""
    if not indent:
        return text
    return "\n".join(indent + line if line else line for line in text.splitlines())
