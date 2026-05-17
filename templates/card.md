---
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

Machine-checkable. The executor runs every item below and the card moves
to `done/` only if every check passes. Optional prose can sit above or
below the fenced block; the executor only parses the fenced block.

Check types supported:

- `shell` -- run a command; exit code 0 means pass.
- `file_exists` -- path exists at repo root (or absolute).
- `file_absent` -- path does not exist.
- `grep_match` -- pattern found in file (or set of files via glob).
- `grep_absent` -- pattern not found.
- `http_status` -- url returns expected status (only when the project
  config opts into network checks).

If a card genuinely needs subjective review that can't be expressed as a
check, mark it tier 5 or 6. The high-stakes path already routes through
Drew's approval, so subjective AC lands where humans look anyway.

```yaml
# Replace the items below. Every check needs a description + a check spec.
# Optional per-item flag:
#   subjective: true  -- only permitted on tier 5 / 6 cards, and at most
#     one such item per card. The runner does not execute subjective
#     items; they route to the human merge gate. See SKILL.md section 4
#     ("AC machinability check") and RUNNER_CONTRACT.md.
acceptance_checks:
  - description: "Lint passes"
    check: { type: shell, cmd: "make lint" }
  - description: "Unit tests pass"
    check: { type: shell, cmd: "make test" }
  - description: "Expected file was created"
    check: { type: file_exists, path: "src/example.py" }
  - description: "New function is referenced from the entry point"
    check: { type: grep_match, file: "src/app.py", pattern: "example_function" }
  # Example of a tier-5/6-only subjective item (do not include on lower
  # tiers; the validator will refuse the batch):
  # - description: "API surface reads cleanly to a senior reviewer"
  #   subjective: true
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
