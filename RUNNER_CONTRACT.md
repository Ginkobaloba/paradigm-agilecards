# Runner contract

The runner is the harness that polls `C:\dev\todo\backlog\`, spawns
executor agents, and drives cards to terminal state. This file is the
contract surface the runner must honor. The /cards skill produces files
that match this contract; any change here is a breaking change for the
runner.

The runner itself is NOT built by the /cards skill task. This doc is
deliberately a description, not an implementation.

---

## Directory invariants

```
C:\dev\todo\
  backlog\       cards that have not been claimed
  active\        cards an executor is currently working on
  amendments\    cards awaiting review of an executor change_request
  done\          cards whose work merged successfully
  blocked\       cards finished but unmerged, or paused on a dependency
  _batches\
    .counter
    b<NNN>-manifest.yaml
```

The subfolder is canonical. The `status:` field in card frontmatter is a
convenience that the runner keeps in sync. If they disagree, the
subfolder wins; the runner repairs the field.

Cards never live in two subfolders. The runner moves the file
atomically between subfolders (filesystem-level file move; on POSIX
that's `rename(2)`, on Windows that's `MoveFileEx` with
`MOVEFILE_REPLACE_EXISTING`) when transitioning state. The filename
itself does not change; only its parent directory changes.

---

## Card status transitions

```
backlog --claim--> active --executor marks finish + verifier pass--> done
                       \--executor marks finish + verifier fail--> active (verifier_notes)
                       \--executor marks finish + verifier skipped (high confidence)--> done
                       \--finish + merge_status!=merged--> blocked
                       \--dependency unmet (re-check)--> backlog
                       \--change_request written--> amendments
amendments --amendment approved--> active
amendments --amendment denied--> active (against original) or blocked
backlog --dependency permanently blocked--> blocked
blocked --unblocked--> backlog or active (runner choice)
done --run_verifier(card_id) manual override--> done (if pass) or active (if fail)
```

The "executor marks finish" arrow is split: a verifier pass (or a
legitimate verifier skip) is what moves the file to `done/`. A verifier
fail moves the card back to `active/` with `verifier_notes` written
into the body for the next executor pass. See "Cold-read verification"
below.

Allowed `status` (subfolder / field) values:

- `backlog`
- `active`
- `amendments` (field value: `awaiting_amendment_review`)
- `done`
- `blocked`

The `awaiting_amendment_review` field value pairs with the
`amendments/` subfolder. Naming asymmetry is deliberate: the
subfolder is short for filesystem ergonomics, the field is explicit
for human readers grepping for state.

Allowed `merge_status` values:

- `pending` -- card hasn't reached merge step yet (initial state)
- `open` -- PR is open, awaiting review or auto-merge
- `merged` -- merged into base_branch
- `requires_review` -- medium tier needs sibling-agent review pass
- `conflict` -- merge conflict with base_branch, needs human or rebase
- `blocked` -- merge gate held by external dependency

A card with `merge_status: merged` is the only kind that belongs in `done/`.

---

## Claim protocol

The runner claims a card by:

1. Checking `depends_on` -- every dependency must be in `done/` with
   `merge_status: merged`.
2. Reading the card's `model_floor` and `pin_required` to allocate an
   executor at the right model.
3. If the project config sets `story_source_path`, re-hash that file
   and compare against the card's `story_hash`. On mismatch, do not
   claim; flag for re-triage (see "Story drift" below).
4. Setting `claimed_by`, `started_at`, and `last_heartbeat` in
   frontmatter.
5. Moving the file from `backlog/` to `active/`.

The claim is an atomic file move between subfolders (filename
unchanged, parent directory changes from `backlog/` to `active/`),
not a lock file. The runner is responsible for not double-claiming.

---

## Heartbeat and orphan reclaim

While a card is in `active/`, the executor updates `last_heartbeat`
periodically (suggested cadence: every 5 minutes, but the runner can
pick). On every runner pass:

- If a card's `last_heartbeat` is older than the project's
  `orphan_timeout_minutes` (default 120), the runner moves the card
  back to `backlog/` and clears `claimed_by`, `started_at`,
  `last_heartbeat`.
- The card's `branch` is left alone (may contain partial work the next
  executor can salvage, or the runner can prune it; up to runner).

This makes executor crashes recoverable without manual intervention.
The card schema reserves the fields; the runner owns the policy.

---

## AC amendment protocol (executor and runner)

The `acceptance_checks:` block of a card is immutable after the card
is written. The executor agent MUST NOT edit it. Attempting to edit
AC is a contract violation; the runner SHOULD detect the edit on
heartbeat and abort the card with a marker in Completion notes.

The supported escape valve is the standup amendment pattern. Executor
side:

1. Detect that an AC item is wrong, ambiguous, or invalidated by
   reality.
2. Append a `change_request:` block to the card body (see SKILL.md
   section 11 for the shape). Do NOT modify the `acceptance_checks:`
   block.
3. Set the card's `status` field to `awaiting_amendment_review`.
4. Update `last_heartbeat` one final time and exit cleanly. Do not
   continue working the card.

Runner side, on noticing a card whose `status` field is
`awaiting_amendment_review`:

1. Move the card from `active/` to `amendments/` (atomic move,
   subfolder change only; filename unchanged).
2. Clear `claimed_by`, `started_at`, `last_heartbeat`. The branch is
   left alone; partial work on the executor's branch is preserved.
3. Notify the human review path (mechanism is runner-defined: a
   marker file, a Slack ping, a queue entry, etc.). The runner does
   not auto-approve.

Reviewer side (human and / or sibling reviewer agent):

- **Approve.** Edit the relevant item inside the card's
  `acceptance_checks:` block: replace the item with the amended
  version and attach provenance fields (`amended_at`, `amended_by`,
  `amendment_reason`, `original:` carrying the pre-amendment item).
  Set status back to `backlog` or `active` per runner choice; the
  runner moves the file accordingly.
- **Deny.** Add a `change_request_decision:` block to the card body
  with reasoning. Move the card back to `active/` (executor finishes
  against the original AC) or `blocked/` (original AC cannot be
  satisfied). Do not delete the `change_request:` block; it stays as
  audit trail.

The runner MUST never amend AC on its own initiative. Amendments are
human-touched by default (sibling reviewer agents may participate but
the approval is recorded against a human or against a reviewer agent
that Drew explicitly delegated to in the project config).

---

## Story drift

The /cards skill stamps every card with `story_hash` = sha256 of the
source story text at plan time. The batch manifest preserves the full
text under `source.text`.

If the project config sets `story_source_path`, the runner:

1. Reads the file at that path on every claim attempt.
2. Computes sha256 of its current contents.
3. Compares against the card's `story_hash`.
4. On mismatch, refuses the claim and moves the card to `blocked/`
   with a marker in the merge_status reason field. Cards in this state
   are awaiting re-triage by another `/cards` invocation against the
   updated source.

Without `story_source_path`, the runner skips this check; `story_hash`
is then just a forensic fingerprint linking the card to the manifest.

---

## Branch and worktree protocol

Default (full mode): one branch per card, named `card/<id>`, based off
`base_branch` (defaults to `main`, planner can override). The executor
commits only to its card branch.

Lean mode (project opts in): all cards in a batch share one branch
`cards/<batch_id>`. Cards merge into that branch sequentially in
dependency order. One PR per batch instead of per card.

**Worktree creation reliability.** When the runner spawns a per-card
worktree (the typical pattern for true parallel execution), it MUST:

1. Serialize worktree creation across parallel runners using a global
   mutex. A file lock on `C:\dev\todo\.runner.lock` (or equivalent) is
   sufficient. This avoids the `.git/config.lock` race observed in
   Claude Code issue #34645 (multiple agents writing config
   concurrently corrupt git state).
2. Verify creation succeeded before handing the worktree to an agent.
   Check (a) the worktree directory exists and is non-empty, (b)
   `git worktree list` includes the path, (c) `git status` inside the
   worktree returns cleanly. If any check fails, treat the claim as
   failed and roll back (move card back to backlog).
3. Document any partial-creation failure in `_batches/<batch>-runner.log`
   so subsequent runs can avoid the same path.

Related Claude Code issues for context: #40164 (parallel agent file
contention) and #34645 (.git/config.lock race). Same mitigations apply.

**Atomic move between subfolders.** Card state transitions rely on
atomic file moves across `backlog/`, `active/`, `amendments/`,
`done/`, `blocked/`.
The filename stays the same; only the parent directory changes. On
NTFS, `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING` is documented
atomic within a volume, but the dev-meta repo includes
`tests/atomic_rename_test.ps1` (named for the syscall it exercises)
which verifies empirically that moving a file between sibling
subfolders is atomic under concurrent contention. Run that script
once per device before trusting parallel runners on that machine. If
the test fails, fall back to `Move-Item` with explicit lock-retry
loop (see test script comments).

---

## Worktree isolation and cross-contamination defense

Parallel executor agents share the same machine and the same git
repo. Without explicit isolation they will step on each other in
subtle ways. The runner MUST enforce the following:

1. **Per-worktree clean env block.** Each spawned executor gets an
   isolated env var block. No env shared across siblings. Inherit
   only the minimum the agent needs (PATH, HOME, TEMP, plus anything
   the project config explicitly lists). In particular, do not share
   `OPENAI_*`, `ANTHROPIC_*`, `AWS_*`, `GH_TOKEN`, or any credential
   var across worktrees unless the project config explicitly says
   so. Treat every executor as if it were running on a fresh host.

2. **Per-worktree dependency caches.** Each worktree gets its own
   `node_modules/`, `__pycache__/`, `.venv/`, `target/`, etc. No
   symlinks to a shared install. Yes this costs disk; the alternative
   is two executors corrupting each other's installs mid-card. Set
   `npm_config_cache` and pip equivalents to a per-worktree path if
   the package manager respects it; otherwise tolerate the duplicate
   downloads.

3. **Per-worktree git config.** Use `git config --worktree` for any
   per-card config (user.name, user.email, commit signing, hooks).
   Do not modify the user-global git config from within an executor.
   The runner MAY pre-seed `--worktree` config before handing the
   worktree to the agent.

4. **Pre-flight clean-state check.** The executor's first action on
   claiming a card is a clean-state assertion: `git status` returns
   clean, no untracked files matching the project's `.gitignore`-able
   patterns, no `.git/*.lock` files present, no leftover process
   locks (lockfiles, PID files) in the worktree from a prior crashed
   agent. If any of those fail, the executor aborts and the runner
   moves the card back to `backlog/` with a marker in Completion
   notes.

5. **Mid-execution log / output isolation.** An in-flight executor's
   logs, intermediate outputs, and scratch files are written to a
   per-card path the runner allocates (suggested:
   `C:\dev\todo\_runs\<trace_id>\`). Sibling agents MUST NOT read
   that path while the card is in `active/` or `amendments/`. The
   runner exposes the path to downstream consumers (review, retro,
   audit) only after the card moves to `done/` or `blocked/`. This
   prevents one agent's half-written log being parsed as ground truth
   by another agent's tool call.

6. **Serialized worktree creation via global mutex.** Worktree
   creation calls (`git worktree add`) must be serialized across
   parallel runners by a global file lock (`C:\dev\todo\.runner.lock`
   or equivalent). Git's internal locks (`.git/config.lock`,
   `.git/worktrees/*/locked`) are not safe under concurrent
   `git worktree add` invocations; see issue #34645. Hold the mutex
   for the duration of the `git worktree add` call only, not for
   the executor's lifetime.

These are not optional. Skipping any of them produces failure modes
that are intermittent, machine-specific, and miserable to debug after
the fact. Pay the isolation cost up front.

---

## Merge gates

Tier-aware. The runner reads `points` (tier 1-6) and routes:

- tier 1, 2: auto-merge if `lint && tests && !conflicts`
- tier 3, 4: auto-merge after a sibling-agent reviewer says ok
- tier 5, 6: open PR, wait for Drew's approval

`pin_required: true` (set from stakes=high) overrides any per-project
relaxation. High-stakes cards always go through human review.

---

## Cold-read verification

Required in v1.2. After the executor marks a card finished and the
acceptance checks pass, the runner spawns a fresh verifier agent
before the file moves to `done/`. The verifier reads the card cold:
no executor conversation history, no planner context, no sibling
chatter. Its input is the card body, the executor's recorded outputs
(completion notes, `actual_tokens`), and access to the project repo.

The verifier re-runs every `acceptance_checks:` item from scratch and
returns one of three results:

- **pass.** All checks pass. The runner stamps `verified_at`,
  `verified_by`, sets `verifier_skipped_reason: null`, and moves the
  card from `active/` to `done/`.
- **fail.** At least one check fails OR the verifier judges the
  executor's claimed work doesn't match what the card actually asked
  for. The runner appends a `verifier_notes:` block to the card body
  (machine-readable list of failed checks plus the verifier's prose
  reasoning), moves the card back to `active/`, and clears
  `claimed_by`, `started_at`, `last_heartbeat`. The next claim picks
  it up with the verifier's notes in hand.
- **error.** The verifier itself crashed or timed out. The runner
  records the error in the runner log and retries the verifier up to
  two times. After two failed retries, the card moves to `blocked/`
  with a marker; do not auto-move to `done/` on verifier error.

### When the verifier MAY be skipped

The runner MAY skip the verifier (auto-pass to `done/`) only when ALL
of the following hold:

1. `cascade_history` is empty (executor did not escalate during the
   card; see "Cascade-on-confidence routing" below).
2. The executor's final self-reported confidence is at least the
   project's `verifier_skip_confidence_threshold` (default `0.9` on a
   0-1 scale; the project config MAY tighten this but MUST NOT relax
   it below `0.9`).
3. Every acceptance check passed on first run (no retries recorded in
   the runner's per-card log).

If all three hold, the runner stamps:

- `verified_at: null`
- `verified_by: null`
- `verifier_skipped_reason: "high-confidence cascade-clean run"`
  (or another short string the runner picks)

Cards in `done/` with both `verified_at: null` AND
`verifier_skipped_reason: null` are a contract violation. The
`/cards validate` subcommand flags them.

### Manual override entry point

The runner MUST expose:

```
run_verifier(card_id: str) -> verifier_result
```

Input is a card id (the runner resolves the file location regardless
of subfolder). Output is a structured result:

```yaml
result: pass | fail | error
reasons: [list of strings; per-check or per-error explanations]
at: 2026-05-17T14:32:00Z
agent_id: verifier-agent-id-or-label
```

Behavior:

- The override runs against the card in its current subfolder. It does
  not require the card to be in `done/`.
- Result handling matches the auto-verifier above: pass updates the
  card's verifier provenance fields and (if the card was in `active/`)
  moves it to `done/`; fail appends `verifier_notes` and (if the card
  was in `done/`) moves it back to `active/`; error retries twice
  then surfaces.
- A manual override NEVER deletes prior verifier state. New
  `verified_at` / `verified_by` values are pushed onto a
  `verifier_history:` list in the card body so the audit trail is
  complete. The top-level frontmatter fields always reflect the most
  recent verification.

This is the contract the dashboard "Run Cold Read Now" button hits.
Any external trigger (CLI, scheduled re-audit, webhook) uses the same
entry point.

### Provenance field schema

Top-level frontmatter fields the runner owns:

- `verified_at` -- ISO 8601 UTC. Null until first verification.
- `verified_by` -- string (agent id or label). Null until first
  verification.
- `verifier_skipped_reason` -- nullable string. Mutually exclusive
  with `verified_at` being non-null.

Body-level audit fields the runner appends:

- `verifier_notes:` -- written on fail. Lists failed checks and prose
  reasoning from the verifier.
- `verifier_history:` -- list of prior verifications when manual
  overrides have run; each entry carries `verified_at`, `verified_by`,
  `result`, and `reasons`.

The skill commits to these field names and shapes. The runner owns
the lifecycle.

---

## Cascade-on-confidence routing

Required runner behavior in v1.2. The planner sets `points` (tier
1-6) at card creation; the runner is permitted to escalate the
executor up to two tiers above that planned value when confidence
falls low at runtime.

### Protocol

1. The executor starts at the planned tier (model and
   `extended_thinking` from `tier_map_claude.yaml` keyed by
   `card.points`).
2. After each meaningful step (the runner defines "step"; suggested:
   per acceptance-check attempt, or per executor self-checkpoint), the
   executor runs a **confidence probe**. The probe is at minimum a
   model self-report scored 0-1. A project MAY layer additional
   signals (test pass count, lint clean status, a domain-specific
   marker flagged in the project's SKILL.md additions); the runner
   combines them per the project config and produces a single
   confidence value.
3. If the combined confidence falls below
   `cascade_escalation_threshold` (default `0.6` on a 0-1 scale,
   tunable per project), the runner escalates one tier (move from
   `card.points` to `card.points + 1`) and re-attempts the step that
   triggered the low confidence.
4. **Maximum escalations: 2.** A card can climb at most two tiers
   above its planned value. After the second escalation, if confidence
   still falls below threshold, the runner halts execution and moves
   the card to `blocked/` with the cascade history attached to
   Completion notes. Do not climb past tier 6; tier 6 is the ceiling
   regardless of where the card started.
5. Each escalation appends to the card's `cascade_history:` field.

### `cascade_history` shape

Top-level frontmatter field, list-typed. Each entry:

```yaml
cascade_history:
  - from_tier: 3
    to_tier: 4
    reason: "low confidence on step 'add rate-limit middleware'"
    confidence_at_escalation: 0.42
    at: 2026-05-17T14:32:00Z
  - from_tier: 4
    to_tier: 5
    reason: "second probe still below threshold after retry"
    confidence_at_escalation: 0.51
    at: 2026-05-17T14:47:00Z
```

The list is append-only within a single card lifetime. If the card is
returned to `backlog/` and re-claimed (e.g., after a verifier fail or
orphan reclaim), the prior `cascade_history` is retained; the next
attempt's escalations append to the same list. The runner does NOT
reset `cascade_history` on re-claim; the history is forensic across
the card's entire run.

### Interactions with other contract surfaces

- **Cold-read verifier.** A non-empty `cascade_history` disqualifies
  the card from verifier-skip. The card always gets a cold read when
  it had to climb.
- **Cost cap.** Escalation can blow past `cost_cap_usd` because higher
  tiers cost more per token. The runner MUST re-evaluate the cap
  immediately after each escalation against the projected remaining
  spend at the new tier. If the projection exceeds the cap, halt and
  move to `blocked/` per the existing cost-cap protocol rather than
  silently overspending mid-escalation.
- **`pin_required`.** Pinning forces the merge gate but does not
  prevent escalation. A pinned tier-3 card may still escalate to
  tier-4 or tier-5 at runtime; the pin still routes to Drew at merge.
- **Model floor.** Escalation only moves UP. The runner never escalates
  below `model_floor`; escalation by definition picks a more capable
  model.

### Configuration

`templates/project_config.yaml` carries the per-project knobs:

- `cascade_escalation_threshold` (default 0.6)
- `verifier_skip_confidence_threshold` (default 0.9)
- `cascade_max_escalations` (default 2, MUST NOT exceed 2 in v1.2)

A project with no entries uses the defaults. A project that opts out
of cascade entirely sets `cascade_max_escalations: 0`; cards then run
strictly at planned tier with verifier always required.

---

## Acceptance check execution

Before a card transitions to `done/`, the runner parses the fenced
`acceptance_checks:` YAML block under the card body's "## Acceptance
criteria" section. Every check in the list must pass.

Check types:

- `shell` -- run `cmd` in the project root; exit code 0 = pass.
- `file_exists` -- `path` exists at repo root (or absolute).
- `file_absent` -- `path` does not exist.
- `grep_match` -- pattern found in `file` (or files matched by glob).
- `grep_absent` -- pattern not found.
- `http_status` -- url returns expected status; only honored when the
  project config explicitly opts into network checks. Disabled by
  default.

Per-check pass/fail is recorded in the runner-appended Completion
notes section. If any check fails, the card moves to `blocked/` and
the failures land in Completion notes for the next executor (or human)
to investigate.

---

## Completion notes

On terminal transition (to `done/` or `blocked/`), the runner appends a
sixth section to the card body titled `## Completion notes` containing:

- what the executor actually did
- workarounds or surprises
- per-check acceptance results
- suggestions for downstream cards

The skill does not write this section; the runner does.

---

## Cost cap enforcement

When a card has `cost_cap_usd` set (non-null), the runner:

1. Tracks cumulative tokens consumed for that card (planner
   attribution, executor model calls, sibling-review pass), broken
   down by model and by input/output. Tokens are the durable
   measurement.
2. On each model-call boundary, converts running tokens to USD by
   reading the current `tier_pricing.yaml` and multiplying. USD is
   derived, never stored on the card.
3. If the derived USD exceeds `cost_cap_usd`, halts execution: move
   card to `blocked/` with a marker in Completion notes recording
   both the token counts and the USD figure at halt. Do not silently
   overspend.

When `cost_cap_usd` is null, no cap is enforced. Per-project caps and
fleet-wide caps are runner concerns and not part of this contract.

---

## Pre-approval gate

When a card has `requires_pre_approval: true` (default for high-stakes
cards), the runner MUST NOT claim the card without an explicit human
approval recorded somewhere the runner can verify (a marker file, a
manifest annotation, a webhook callback -- runner picks the mechanism).

This is distinct from `pin_required`, which only forces the merge gate.
A card can be pre-approved to run but still require human approval at
merge time.

---

## Trace id propagation

Every card carries `trace_id` (uuid). The runner MUST propagate this id
to:

- every sub-agent invocation the executor makes
- every log event the runner emits for this card
- the merge gate (PR title or commit trailer suggested)
- any cost-tracking ledger entry

This is the only practical way to correlate logs across a fleet of
parallel executors after the fact.

---

## Context discipline (hard constraint)

The executor's prompt context, when the runner spawns it, MUST include
only:

- the card body (in full)
- the project repo (working tree)
- the `trace_id`

The runner MUST NOT forward:

- the batch manifest
- sibling cards (even cards in `depends_on`)
- planning-pass conversation
- planner-reviewer disagreements

If the executor needs information from a dependency, that information
is either (a) committed code from the dependency's branch that the
executor can read normally, or (b) explicitly summarized in the card's
Pointers section. Quadratic context explosion is a known failure mode
in multi-agent systems; this constraint stops it at the spawn site.

---

## Cards are state, the runner is stateless

The card is the durable unit of state. The runner MUST NOT hold task
state in its own context window or in-memory caches. To answer "what
is the state of card X" the runner reads the card.

Concretely:

- No in-memory map of "card id -> state."
- No long-lived process holding the active backlog in working set.
- Every claim attempt re-reads the card frontmatter from disk.
- Every status check re-reads the subfolder location from disk.
- Every dependency resolution re-reads the dependency card from disk.

Cost: one filesystem read per query (cheap on local NTFS, cheap enough
on a network share). Benefit: the runner can crash, restart, scale out
horizontally, or be killed and resumed by another process without
losing or corrupting any card's state. Cards survive runners.

This is also what makes the planner-vs-reality feedback loop possible
across sessions: the actual_* fields on a card are durable evidence
that any future planner can read, regardless of which orchestrator
recorded them.

---

## What the skill commits to

- Frontmatter schema as defined in `templates/card.md`. Field renames or
  removals are breaking changes.
- Status subfolder names: exactly `backlog`, `active`, `amendments`,
  `done`, `blocked`, `_batches`.
- Status field values: `backlog`, `active`, `awaiting_amendment_review`,
  `done`, `blocked`. The `awaiting_amendment_review` field value
  pairs with the `amendments/` subfolder.
- Tier semantics: `points` is 1-6, mapping to the matrix in `README.md`.
- Manifest schema as defined in `templates/batch_manifest.yaml`.
- Batch id format: `b<NNN>` zero-padded, counter in `_batches\.counter`.
- `story_hash` is sha256 hex of `source.text` from the manifest, stamped
  identically on every card in the batch.
- `acceptance_checks:` YAML block lives inside the body's "## Acceptance
  criteria" section, fenced as `yaml`. The schema for individual checks
  is fixed (see template + this doc).
- `last_heartbeat` is ISO 8601 UTC, nullable.
- `trace_id` is a v4 uuid stamped at card creation.
- `cost_cap_usd` is nullable float or null.
- `requires_pre_approval` is bool, defaults true when stakes is high.
- `estimated_tokens` and `estimated_duration_minutes` are set by the
  planner at card creation.
- `actual_tokens` is set by the executor at completion;
  `actual_duration_minutes` is derived from `started_at` and
  `finished_at`. Cards must carry these fields so the planner-vs-
  reality feedback loop can read them post-hoc.
- Cards do NOT store USD. USD is derived from tokens at display or
  cap-check time using the current `tier_pricing.yaml`. The only
  USD field on a card is `cost_cap_usd` (the budget ceiling),
  because budgets are how humans think about spend.
- DAG of `depends_on` edges is acyclic. The skill refuses to write a
  batch with a cycle.
- Cards are the system's state. Any orchestrator (this skill, a
  runner, future coordinators) reads card state from disk per query
  and holds no in-memory mirror.
- Cold-read verifier provenance fields are present on every card:
  `verified_at` (nullable ISO 8601 UTC), `verified_by` (nullable
  string), `verifier_skipped_reason` (nullable string). The runner
  owns the lifecycle; the skill commits to the names and shapes.
- `cascade_history` is a list-typed frontmatter field present on every
  card; planner stamps it as `[]` at creation, runner appends one
  entry per escalation. Entry shape is `{from_tier, to_tier, reason,
  confidence_at_escalation, at}`.
- `subjective: true` is a permitted per-item flag inside
  `acceptance_checks:` for tier 5 / 6 cards. The runner does not
  attempt to execute items so marked; they route to the human merge
  gate instead.

---

## What the skill does not commit to

- How the runner spawns executors, polls, or scales.
- How sibling-agent review is implemented.
- Whether PRs go through GitHub, gitea, or local-only.
- Retry policy on executor failure.

The runner owns those choices.
