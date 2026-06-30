"""Structured-output client that emits a replacement AC item.

Chunk 6a's `auto_edit_ac: true` flow runs in two phases:

1. The existing `AnthropicSiblingReviewerClient` returns
   `approve`/`request_changes`/`comment`.
2. ONLY on `approve` (with sufficient confidence) does this module
   fire: a separate structured-output call that emits the full
   replacement item as JSON. The output is validated against a small
   schema; on failure the runner falls back to the chunk 5
   "park-in-blocked" path so a human can finalize.

We use the Anthropic SDK's tool-use channel for structured output:
the assistant is told its ONLY tool is `propose_ac_amendment`, the
tool's input schema describes the replacement item, and the runner
reads `tool_use.input` rather than free-form text. This is the same
pattern the chunk-3 executor tool belt uses; it gives stronger
guarantees than free-form JSON parsing for fields like `ac_index`
(int) and `check_fields` (mapping).

Pluggable: tests inject a `StaticAmendmentEditorClient` with scripted
outputs; production uses `AnthropicAmendmentEditorClient` against
the SDK. The protocol is intentionally narrow -- only `edit(...)`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..common.project_config import ReviewerConfig
from .ac_editor import AmendmentEdit
from .reviewer_cost import ReviewerUsage


log = logging.getLogger(__name__)


class AmendmentEditorClient(Protocol):
    """Pluggable structured-output editor for AC amendments.

    Returns None when the editor declines to propose an edit (low
    confidence, ambiguous change_request, refusal). The
    amendment_reviewer treats None the same as a low-confidence edit:
    fall back to the human-finalize path.
    """

    def edit(
        self,
        *,
        card_id: str,
        card_body: str,
        change_request: str,
        ac_items: list[dict[str, Any]],
        reviewer: ReviewerConfig,
    ) -> AmendmentEdit | None: ...


# ---- static (test) client -------------------------------------------


@dataclass
class StaticAmendmentEditorClient:
    """Pre-canned editor outputs for tests.

    `edits_by_card` overrides on a per-card basis; `default` covers
    the rest (None == "no edit available, fall back"). Recorded calls
    go onto `calls` for assertion.
    """

    default: AmendmentEdit | None = None
    edits_by_card: dict[str, AmendmentEdit | None] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def edit(
        self,
        *,
        card_id: str,
        card_body: str,
        change_request: str,
        ac_items: list[dict[str, Any]],
        reviewer: ReviewerConfig,
    ) -> AmendmentEdit | None:
        self.calls.append({
            "card_id": card_id,
            "card_body": card_body,
            "change_request": change_request,
            "ac_items": ac_items,
            "reviewer": reviewer,
        })
        if card_id in self.edits_by_card:
            return self.edits_by_card[card_id]
        return self.default


# ---- anthropic-backed client ----------------------------------------


# The tool the structured-output editor MUST call. The schema is the
# contract between the runner and the LLM: anything the editor wants
# the runner to do flows through this shape. Any extra keys land in
# `check_fields` (the handler-specific item body).
_EDIT_TOOL_NAME = "propose_ac_amendment"
_EDIT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ac_index": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Zero-based index of the AC item being replaced. Must "
                "name an item that currently exists on the card."
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "Post-amendment human-readable description for the item. "
                "Required."
            ),
        },
        "check_type": {
            "type": "string",
            "description": (
                "Canonical type name for the AC item, e.g. "
                "`file_exists`, `command`, `file_contains`, `subjective`."
            ),
        },
        "check_fields": {
            "type": "object",
            "description": (
                "All handler-specific fields for the item except "
                "`description` and `type` (those are top-level). For "
                "`file_exists` this is just `{path: '...'}`; for "
                "`command` it includes `command`, `expected_exit_code`, "
                "etc."
            ),
            "additionalProperties": True,
        },
        "amendment_reason": {
            "type": "string",
            "description": (
                "Short prose explaining why this change. One or two "
                "sentences; written into the card's `amendment_reason:` "
                "provenance field."
            ),
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": (
                "Your confidence in this exact replacement, 0.0-1.0. The "
                "runner refuses edits below the project's "
                "`auto_edit_confidence_floor` (default 0.85) and falls "
                "back to a human finalize. Be honest about uncertainty."
            ),
        },
    },
    "required": [
        "ac_index",
        "description",
        "check_type",
        "amendment_reason",
        "confidence",
    ],
}


_SYSTEM_PROMPT_BASE = """You are the AC amendment editor for an agile-cards
runner. A peer reviewer just approved a change_request: block on a card.
Your job: emit ONE replacement acceptance-criteria item that satisfies
the change_request and is consistent with the card body. The runner
will splice your replacement into the card's `acceptance_criteria:`
list at the index you name, with provenance fields attached.

Rules:

- You MUST call the `propose_ac_amendment` tool exactly once. Do not
  emit a free-form text response; the runner will ignore it.
- Pick `ac_index` to match the item the change_request talks about.
  The change_request may name the index, or may describe it; if it
  describes, choose the index whose current `description` best
  matches.
- The replacement should be the *amended* item, not the original.
  Move only what the change_request asks to move; do not refactor
  surrounding AC.
- `check_type` must be a canonical handler type. The runner does NOT
  validate handler-field shape at splice time -- the verifier catches
  a bad shape on the next claim. But emit fields that the named
  handler expects; a half-formed item just punts the failure
  downstream.
- Be honest with `confidence`. Below 0.85 the runner ignores your
  edit and routes the card to a human anyway, so a low-confidence
  edit is wasted tokens.
"""


@dataclass
class AnthropicAmendmentEditorClient:
    """Editor client backed by the Anthropic SDK tool-use channel.

    `client` is an `anthropic.Anthropic` instance. Constructed by the
    daemon's `_build_amendment_editor_client()`; tests inject the
    static variant instead.
    """

    client: Any  # anthropic.Anthropic
    max_tokens: int = 1024

    def edit(
        self,
        *,
        card_id: str,
        card_body: str,
        change_request: str,
        ac_items: list[dict[str, Any]],
        reviewer: ReviewerConfig,
    ) -> AmendmentEdit | None:
        system_prompt = _system_prompt(reviewer)
        user_prompt = _user_prompt(card_id, card_body, change_request, ac_items)
        if len(user_prompt) > 64000:
            user_prompt = user_prompt[:64000] + "\n\n[...body truncated...]"
        tool_def = {
            "name": _EDIT_TOOL_NAME,
            "description": (
                "Propose ONE replacement acceptance-criteria item for "
                "the card. The runner will splice it in with provenance."
            ),
            "input_schema": _EDIT_TOOL_SCHEMA,
        }
        try:
            response = self.client.messages.create(
                model=reviewer.model_id,
                max_tokens=self.max_tokens,
                system=system_prompt,
                tools=[tool_def],
                tool_choice={"type": "tool", "name": _EDIT_TOOL_NAME},
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("amendment editor SDK call failed for %s: %s", card_id, exc)
            return None
        usage = ReviewerUsage.from_response(response, model_id=reviewer.model_id)
        return _extract_edit(response, model_id=reviewer.model_id, usage=usage)


def _system_prompt(reviewer: ReviewerConfig) -> str:
    if reviewer.prompt_extra:
        return _SYSTEM_PROMPT_BASE + "\n" + reviewer.prompt_extra
    return _SYSTEM_PROMPT_BASE


def _user_prompt(
    card_id: str,
    card_body: str,
    change_request: str,
    ac_items: list[dict[str, Any]],
) -> str:
    """Render the user message. The AC items are dumped as compact JSON
    with indices so the model can name `ac_index` precisely."""
    ac_dump = json.dumps(
        [{"index": i, "item": item} for i, item in enumerate(ac_items)],
        indent=2,
        default=str,
    )
    return (
        f"# Card `{card_id}`\n\n"
        "## Card body\n\n"
        f"{card_body}\n\n"
        "## Current acceptance_criteria (indexed)\n\n"
        "```json\n"
        f"{ac_dump}\n"
        "```\n\n"
        "## Reviewer-approved change_request\n\n"
        "```yaml\n"
        f"{change_request}\n"
        "```\n"
    )


def _extract_edit(
    response: Any, *, model_id: str, usage: ReviewerUsage | None = None
) -> AmendmentEdit | None:
    """Pull the tool_use input off an Anthropic response.

    Returns None if the model failed to call the tool (a refusal, a
    free-text response despite the `tool_choice` constraint, or a
    transport oddity).
    """
    content = getattr(response, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type != "tool_use":
            continue
        if getattr(block, "name", None) != _EDIT_TOOL_NAME:
            continue
        payload = getattr(block, "input", None)
        if not isinstance(payload, dict):
            return None
        return _payload_to_edit(payload, model_id=model_id, usage=usage)
    return None


def _payload_to_edit(
    payload: dict[str, Any],
    *,
    model_id: str,
    usage: ReviewerUsage | None = None,
) -> AmendmentEdit | None:
    """Coerce the tool_use input into a frozen `AmendmentEdit`.

    Defensive: a model that violates the schema (returns a string for
    ac_index, omits a required field) is treated as "no edit" rather
    than silently producing a malformed splice.
    """
    try:
        ac_index = int(payload["ac_index"])
        description = str(payload["description"]).strip()
        check_type = str(payload["check_type"]).strip()
        amendment_reason = str(payload["amendment_reason"]).strip()
        confidence = float(payload["confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("amendment editor returned malformed payload: %s", exc)
        return None
    raw_fields = payload.get("check_fields") or {}
    if not isinstance(raw_fields, dict):
        log.warning(
            "amendment editor returned non-mapping check_fields: %r",
            type(raw_fields).__name__,
        )
        return None
    if not description or not check_type:
        log.warning("amendment editor returned empty description / check_type")
        return None
    return AmendmentEdit(
        ac_index=ac_index,
        description=description,
        check_type=check_type,
        check_fields=dict(raw_fields),
        amendment_reason=amendment_reason,
        confidence=max(0.0, min(1.0, confidence)),
        model_used=model_id,
        actual_cost_usd=usage.cost_usd if usage is not None else None,
        input_tokens=usage.input_tokens if usage is not None else 0,
        output_tokens=usage.output_tokens if usage is not None else 0,
    )
