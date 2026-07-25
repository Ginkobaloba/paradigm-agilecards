---
# Schema version this card was written against. Required on cards
# planned by v1.3 or later. The verifier reads this to dispatch to
# the matching parse path; cards without it are treated as legacy
# v1.2 and routed through the deprecated-alias table.
verifier_schema_version: "1.3"

# Stable id. Lowercase, hyphens, ASCII. Format: <batch>-<NN>-<verb-noun>.
id: b000-00-replace-me

# Short imperative. One line.
title: Replace me with an action-verb title

# Absolute project path. Runner uses this to locate or create the project.
project: C:\dev\project-example

# Mirrors the canonical subfolder. Keep in sync with the file's location.
# One of: backlog, active, awaiting_amendment_review, done, blocked.
# (The awaiting_amendment_review field value pairs with the amendments/
# subfolder. See RUNNER_CONTRACT.md "Card status transitions".)
status: backlog

# Tier 1-6. Derived from stakes + difficulty per the README matrix.
points: 3

# Stakes axis: low / medium / high. Sets model_floor and pin_required.
stakes: medium

# Difficulty axis: shallow / deep. Sets extended_thinking via tier map.
difficulty: shallow

# Abstract LLM-agnostic version of extended_thinking.
thinking_depth: shallow

# Concrete model from tier_map_claude.yaml.
model: claude-sonnet-4-6

# Mirror from tier map.
extended_thinking: false

# Hard family floor. Runner cannot pick anything cheaper than this.
# One of: haiku, sonnet, opus.
model_floor: sonnet

# True when stakes=high. Forces human approval on merge regardless of
# project-level config.
pin_required: false

# Whether the runner is allowed to START this card without explicit
# human approval. Broader than pin_required (which gates the merge
# path). Defaults true when stakes is high. Use this for cards where
# even the planning was risky enough that you want eyes before
# anything runs.
requires_pre_approval: false

# Optional cost ceiling in USD for cumulative spend on this card
# (planning + executor + any sibling-review pass). Runner halts if
# tracked spend exceeds the cap. null = no cap. The planner can set
# this defensively for known-expensive tiers; leave null otherwise.
cost_cap_usd: null

# Planner-vs-reality feedback loop. The planner sets the estimated_*
# fields at card creation. The executor sets the actual_* fields when
# the card finishes. Deltas inform future tier-rubric tuning.
#
# Tokens are the immutable record. USD is a derived view, computed at
# display or cap-check time by multiplying tokens against the current
# tier_pricing.yaml. Old cards stay correct when prices change.
# See README "Tokens immutable, USD derived" and /cards stats
# (future) for accuracy-over-time reporting.
estimated_tokens: 0          # planner's total token budget for the card
actual_tokens: null           # executor: sum(input + output tokens)
estimated_duration_minutes: 0 # planner's wall-clock estimate
actual_duration_minutes: null # derived from started_at / finished_at

# UUID generated at card creation, propagated to every sub-agent call
# and log event for cross-system correlation. Replace with a real uuid
# when filling in this template programmatically.
trace_id: 00000000-0000-0000-0000-000000000000

# One-line read of both axes, for humans skimming the file.
sizing_note: "medium stakes, shallow difficulty -- sonnet without thinking"

# Hard prerequisites only. List of card ids.
depends_on: []

# Files or globs this card will modify. Runner uses this to flag touch
# conflicts with sibling cards.
touches:
  - src/example.py

# Planner-declared scope envelope for the confidence merge gate (gate-5).
# Files or globs the card's diff is expected to stay within. The gate
# reads this as a soft signal: when present and the whole diff fits
# inside it, the card earns a small confidence bonus; a diff that strays
# outside the envelope earns nothing. Optional and additive -- omit it
# (or leave it empty) and the signal simply contributes 0, with no
# behavior change. Distinct from `touches:` (sibling-conflict
# detection): `expected_files` is the gate's scope check and may be
# broader (e.g. a directory glob) to fence the card's blast radius.
expected_files:
  - src/example.py
  - tests/**

# The /cards batch this card came from. Links to the manifest.
batch: b000

# sha256 hex of the source story text at plan time. The manifest carries
# the full text. If the source story changes (e.g. RFC edited after
# planning), the runner can detect mismatch and flag the card for
# re-triage instead of executing stale intent.
story_hash: REPLACE_WITH_SHA256_HEX

# ISO date of creation.
created: 2026-05-16

# Null until the runner claims the card.
started_at: null
finished_at: null
claimed_by: null
model_used: null

# Heartbeat. Executor updates this periodically (e.g. every 5 minutes)
# while it works the card. The runner uses it for orphan reclaim: if a
# card sits in active/ with a heartbeat older than orphan_timeout_minutes
# (default 120, configurable per project), it's moved back to backlog/
# and claimed_by / started_at / last_heartbeat are cleared.
last_heartbeat: null

# Per-card feature branch (full mode) or shared cards/<batch> (lean mode).
branch: card/b000-00-replace-me

# What this branch is built off of.
base_branch: main

# Merge gate state. See RUNNER_CONTRACT.md for the state machine.
# One of: pending, open, merged, requires_review, conflict, blocked.
merge_status: pending

# Cold-read verifier provenance. The runner owns these fields. They are
# null until a verifier pass runs OR until the verifier is legitimately
# skipped (high-confidence cascade-clean run; see RUNNER_CONTRACT.md
# "Cold-read verification"). On a manual run_verifier(card_id) override,
# the runner pushes the prior values onto a verifier_history: list in
# the body and updates these fields to the latest pass.
verified_at: null            # ISO 8601 UTC, nullable
verified_by: null            # agent id / label, nullable
verifier_skipped_reason: null  # nullable string; mutually exclusive
                               # with verified_at being non-null

# Cascade-on-confidence history. Append-only list across the card's
# entire run (including re-claims after verifier fail or orphan
# reclaim). Each entry: {from_tier, to_tier, reason,
# confidence_at_escalation, at}. Empty list at creation. See
# RUNNER_CONTRACT.md "Cascade-on-confidence routing".
cascade_history: []

# Verifier subjective-evaluator cascade history. Append-only list
# across every subjective verification pass. Each entry:
# {tier_attempted, model, confidence, result, reasoning, at,
# item_idx}. Empty list at creation. Mirrors cascade_history's
# audit-forever stance but for the subjective evaluator instead of
# the executor. See RUNNER_CONTRACT.md "Cold-read verification".
verifier_cascade_history: []

# If the subjective cascade exhausts without reaching the confidence
# threshold, the runner moves the card to awaiting_standup_review/
# and writes this field naming the AC item(s) that could not be
# resolved. Null in every other state.
standup_reason: null

---

## Context

Two to four sentences. What is the user story or discussion this card
came from? What does the executor need to understand about the world
before touching anything? Keep it short. The executor is amnesiac and
will read only this card.

## Scope

Concrete, bounded list of what to do. Bullet points are fine here because
the executor needs a checklist, not prose. Be specific about files,
functions, behaviors. If the scope can't be stated in five bullets, the
card is too big; split it.

## Out of scope

Explicit list of what NOT to do. This is the single most important section
for parallel work. Without it, two cards working on adjacent areas will
both expand into the same code and step on each other. State the things
that sound related but belong to other cards.

## Acceptance criteria

Machine-checkable. The verifier dispatches each item to its registered
handler. The card moves to `done/` only if every item passes. A card
with NO parseable acceptance criteria FAILS closed (a card that verifies
nothing is never merged), so every card needs at least one real item.
Subjective items route through the cascade evaluator described in
RUNNER_CONTRACT.md "Cold-read verification".

Canonical types. The single source of truth is the type the DAEMON runs:
`cards_runner/verifier/types.py` (`CANONICAL_TYPES`). There are exactly
six. Anything else -- including `command`, `python_assert`, `http_status`,
`http_contains`, `file_absent_content` (names from the older
`lib/verifier` reference implementation) -- is rejected with a
`SchemaError` and the card fails. If you need a shell check, use `shell`,
not `command`.

Filesystem family:

- `file_exists` -- path exists at worktree root (relative) or
  absolute. Required: `path`.
- `file_absent` -- path does not exist. Required: `path`.
- `file_contains` -- file contains a regex/substring. Required: `path`
  and `pattern`. Optional: `case_sensitive` (default true).
- `file_lacks` -- file does NOT contain `pattern`. Same shape as
  `file_contains`. (Legacy alias: `grep_absent`.)

Process family:

- `shell` -- run a shell command; passes when it exits 0 (or matches
  `expect_exit:`). Required: `command` (str). Optional: `expect_exit`
  (default 0). Runs in the worktree with the scrubbed env block, via
  `cmd.exe /c` on Windows and `sh -c` on POSIX. (Legacy alias: `shell`.)

Human-judgment family. Tier 5 / 6 cards only:

- `subjective` -- evaluator returns strict pass/fail with a
  confidence score; the orchestrator cascades haiku -> sonnet -> opus
  if confidence falls below threshold. Required: `description`,
  `evidence_required` (string telling the executor what to paste into
  the `subjective_evidence:` block).

```yaml
# Replace the items below. Every item needs `description` and `type`
# plus the type-specific fields. The schema validator refuses a batch
# where any item declares an unknown type, and a card with zero items
# fails closed -- so keep at least one real check here.
acceptance_criteria:
  - description: "Lint passes"
    type: shell
    command: "make lint"
  - description: "Unit tests pass"
    type: shell
    command: "make test"
  - description: "Expected file was created"
    type: file_exists
    path: "src/example.py"
  - description: "New function is referenced from the entry point"
    type: file_contains
    path: "src/app.py"
    pattern: "example_function"
  - description: "No leaked api keys remain"
    type: file_lacks
    path: "src/config.py"
    pattern: "api_key\\s*=\\s*['\"][^'\"]+['\"]"
  # Tier 5 / 6 only. Executor populates the matching entry in the
  # subjective_evidence: block below.
  # - description: "Error response reads cleanly to a senior reviewer"
  #   type: subjective
  #   evidence_required: "paste a rendered error response from the new path"
```

## Subjective evidence

Executors completing tier 5 / 6 cards with `subjective` AC items
populate a structured `subjective_evidence:` block in their completion
notes, keyed by the AC item's index in `acceptance_criteria`. The
evaluator reads this verbatim. Free-form prose alongside the
structured block is fine; only the structured block is parsed.

```yaml
subjective_evidence:
  index_5: |
    Rendered error response from POST /v1/foo with empty body:
      HTTP/1.1 400 Bad Request
      {"error": {"code": "missing_field", "field": "name",
                 "hint": "name is required and must be 1-64 chars"}}
```

## Pointers

Anything the amnesiac executor needs to find context fast:

- relevant files and globs
- the originating user-story fragment (paste it here)
- related cards (`depends_on` is the hard list; this is the soft list)
- design docs, RFCs, prior handoffs

The runner appends a sixth section, "Completion notes," when moving the
card to `done/` or `blocked/`. Do not write that section here at creation
time.
