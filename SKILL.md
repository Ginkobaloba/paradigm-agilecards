---
name: cards
description: |
  Decompose a user story or pasted discussion into independent,
  claimable cards with tier-derived model recommendations, suitable
  for parallel agent execution. Triggers on /cards, "break this into
  cards", "decompose this story into parallel work", "plan this as
  cards", or any request that names cards/backlog/parallel execution
  in a planning context. Produces cards in C:\dev\todo\backlog\ and a
  manifest in C:\dev\todo\_batches\. Does NOT execute the cards; the
  runner does that (see RUNNER_CONTRACT.md).
tools: Read, Write, Edit, Glob, Grep, Bash, Agent
---

# /cards

The /cards skill is a planner. It takes a user story or pasted
discussion, runs a planner + reviewer (or decomposer + estimator +
reviewer) pass, and writes a batch of cards to `C:\dev\todo\backlog\`
with a manifest in `C:\dev\todo\_batches\`. The runner (separate
component, out of scope) picks up the cards from there.

The /cards skill never executes cards. It plans them. Confusing these
two responsibilities is the most common way the system ends up
brittle.

---

## 0. Inputs

- A user story (one paragraph or longer)
- A pasted discussion (chat log, design doc excerpt, meeting notes)
- A path to a markdown doc to ingest

Flags:

- `--project <path>` -- absolute project path. If omitted, infer from
  the current working directory or ask.
- `--deep` -- force the 3-agent planning variant regardless of input
  size.
- `--lean` -- override project config; commit all batch cards to a
  shared `cards/<batch_id>` branch.
- `validate` -- subcommand. Skips planning and runs the
  status-vs-subfolder integrity check across `C:\dev\todo\`.

---

## 1. Resolve context

1. Read `C:\dev\NAMING_CONVENTIONS.md`. If missing, fall back to
   `C:\dev\_meta\NAMING_CONVENTIONS.md`. If both missing, abort.
2. Read `C:\dev\SESSION_PROTOCOL.md` (same fallback).
3. Determine the target project path from `--project` or the current
   working directory.
4. If `<project>\.cards-config.yaml` exists, parse it for mode (full
   or lean), `orphan_timeout_minutes`, `story_source_path`,
   `hot_paths`, and merge-gate overrides.
5. Ensure `C:\dev\todo\` and its subfolders exist. Create
   `backlog/`, `active/`, `done/`, `blocked/`, and `_batches/` if any
   are missing. Touch `_batches\.counter` and seed it to `0` if
   absent.

---

## 2. Planning pass

Default: two agents, both pinned to `claude-opus-4-7`, both with
extended thinking enabled. Both invoked directly via the Agent tool.

- **Planner** -- proposes the decomposition. For each proposed card,
  it sets title, scope, out-of-scope, dependencies, touches, and a
  first-pass stakes/difficulty read.
- **Reviewer** -- adversarial. Looks for missing dependencies,
  ambiguous scope, undersized or oversized cards, and parallel-hazard
  pairs (sibling cards that share files).

Run them in parallel (one Agent call per role, single message with
two tool uses). The reviewer's critique is returned to the planner
for one revision round. Stop after that round. Two rounds is the
budget; further rounds buy little and burn tokens.

Three-agent variant fires when:

- input exceeds the project's `deep_plan_token_threshold` (default
  ~3000 tokens), or
- the user passes `--deep`.

Roles for the deep variant:

- **Decomposer** -- raw breakdown into atomic units
- **Estimator** -- sizes each card on stakes + difficulty, writes
  the sizing_note
- **Reviewer** -- adversarial, same role

Unresolved disagreements between any two planning agents are logged
verbatim in the manifest under `planner_disagreements:`. Never silently
average; Drew triages from the manifest.

**Done definition is a planner output, machine-verifiable, no
interpretation allowed.** One of the planner's required outputs for
every card is its Done definition: the `acceptance_checks:` YAML block
inside the body's Acceptance criteria section. Every item in that
block MUST be a runnable assertion (`shell`, `file_exists`,
`file_absent`, `grep_match`, `grep_absent`, `http_status`), not prose.
A card whose "done" cannot be expressed as runnable assertions either
needs to be split until its sub-cards can, or marked tier 5 / 6 so the
high-stakes path puts a human at the merge gate where subjective
review belongs. The validation pass in section 4 enforces this.

**AC is immutable post-creation.** Once a batch is written, the
`acceptance_checks:` block of a card cannot be edited by the executor
agent. If the executor needs to argue for an amendment (e.g., a test
turns out to be wrong, an assumption was off), the mechanism is the
standup amendment pattern (see section 11). The executor's voice is
preserved because they are closest to the work; the immutability is
the guard against an agent rewriting the goalposts mid-run.

**Future optimization note (not committed for v1.1).** Planning is
multi-agent by default: Planner + adversarial Reviewer, both at opus
with extended thinking. A staged variant exists as a token-efficiency
lever, to be explored once `/cards stats` produces estimated-vs-actual
data: stage 1 sonnet-no-ET decomposition pass, stage 2 opus AC and
tier assignment, stage 3 opus or sonnet+ET adversarial review. Not a
v1.1 change. Flagged here so the data the system collects can inform
the decision later instead of vibes.

**Cascade-on-confidence is a runtime concern, not a planning concern.**
The planner sets `points` (tier) per the matrix in section 3. At
execution time, the runner is permitted to escalate the executor up to
two tiers above that planned value when the executor's self-reported
confidence falls below a configurable threshold. The full protocol
(probe shape, threshold, escalation limit, `cascade_history` field)
lives in `RUNNER_CONTRACT.md`. The planner does not need to predict
escalation; it only needs to set the best initial tier it can.

---

## 3. Tier assignment

For every card the planning pass produced:

1. Set `stakes` (low / medium / high) and `difficulty` (shallow /
   deep) per the planner's read.
2. Derive `points` (tier 1-6) from the matrix in README.md.
3. Look up `model` and `extended_thinking` in `tier_map_claude.yaml`
   for that tier.
4. Set `model_floor` from stakes (low -> haiku, medium -> sonnet,
   high -> opus).
5. Set `pin_required = (stakes == "high")`.
6. Set `requires_pre_approval = (stakes == "high")` by default; the
   planner may set true for medium-stakes cards if it judged the
   planning itself was risky.
7. Set `cost_cap_usd` if the tier has a documented historical blow-up
   risk; otherwise leave null. The runner enforces this cap by
   tracking actual tokens consumed and converting on demand via
   `tier_pricing.yaml`; the cap is a USD ceiling, the underlying
   measurement is tokens.
8. Generate a v4 uuid for `trace_id`.
9. Compute `sizing_note` -- a one-line read of both axes.

---

## 4. Validation

Before writing anything, run these checks. If any fail, surface the
failure and abort. No half-written state in `C:\dev\todo\`.

1. **DAG cycle detection.** Walk the `depends_on` graph. If there is
   a cycle, refuse to write the batch. Surface the cycle as
   `[card_a -> card_b -> ... -> card_a]`. A cyclic dependency is a
   planning bug; never let it reach the runner.
2. **Earn-its-keep heuristic.** If the planner produced fewer than 5
   cards, or if the dependency graph is fully linear with zero
   parallelism, abort with a one-line explanation. /cards is not
   meant to manufacture ceremony for work that doesn't need it.
3. **Parallel-hazard scan.** For each pair of cards with no
   `depends_on` edge between them, intersect their `touches:` lists
   (expanding globs). Pairs with non-empty intersection are
   parallel-hazardous; record them in the manifest's
   `parallel_hazards:` block. This doesn't block; it informs the
   runner so it can serialize the pair.
4. **Hot-paths cross-check.** For any card whose `touches:` matches a
   project-config `hot_paths:` glob, raise the card's parallel
   sensitivity. Surface in the dry-run summary.
5. **AC machinability check.** For each card, parse the
   `acceptance_checks:` YAML block. Every item must have a `check:`
   field whose `type` is one of the supported runnable types (`shell`,
   `file_exists`, `file_absent`, `grep_match`, `grep_absent`,
   `http_status`). Prose-only AC items cause an abort with the
   offending card id and item index. The exception is tier 5 / 6
   cards, which may carry up to one subjective item per card because
   the merge gate already routes through a human; the planner must
   mark that item with `subjective: true` so the runner does not
   attempt to execute it.

---

## 5. Dry-run summary

Render to the user, no files written yet:

- Card count and points histogram
- Dependency edges (compact list)
- Count of immediately-claimable cards (no unmet deps)
- Parallel-hazard pairs
- Planner-reviewer disagreements (if any)
- The proposed batch id (next from `.counter`)
- Project path and mode (full / lean)

Stop and wait for explicit approval. Approval is a textual "ok",
"go", "approved", "yes". Anything else is a request for revision or
abort.

If the user requests revision, return to step 2 with their feedback
attached as additional planner input. Cap revisions at three. If
revisions don't converge by then, surface the disagreement and ask
Drew to break the tie.

---

## 6. Write phase

On approval:

1. Lock `_batches\.counter` (atomic-file-move based; see
   RUNNER_CONTRACT.md's "Atomic move between subfolders" section).
   Increment, allocate the new batch id, release.
2. For each card, generate the file at
   `C:\dev\todo\backlog\<id>.md`. Frontmatter is the full schema in
   `templates/card.md`, populated from the planning output. Body
   sections are filled by the planner (Context, Scope, Out of scope,
   Acceptance criteria, Pointers).
3. Write the manifest at `C:\dev\todo\_batches\<batch>-manifest.yaml`
   per `templates/batch_manifest.yaml`. Include the full source text,
   `story_hash` (sha256 of source.text), planning agents and models
   used, planner disagreements, summary numbers, all cards, all
   dependency edges, all parallel hazards.
4. If `C:\dev\todo\` is under git (dev-meta typically isn't tracking
   todo/ at the moment), stage and commit with message
   `cards: plan batch b<NNN> (<N> cards) for <project>`.

---

## 7. Failure modes

- **Empty input** -- nothing to plan; explain and exit.
- **Under 5 cards or fully linear graph** -- /cards refuses; suggest
  doing the work directly or reformulating.
- **Cyclic dependency** -- planning bug; refuse and surface the cycle.
- **Missing tier_map** -- explain and abort; do not guess.
- **Missing protocol files (NAMING_CONVENTIONS, SESSION_PROTOCOL,
  both copies)** -- explain and abort; do not guess.
- **Batch id collision** -- recompute from `.counter`; if still
  collides, abort and surface.
- **Write conflict in `backlog/`** -- abort; never overwrite a
  pre-existing card.

In every failure path, `C:\dev\todo\` is left in the same state it
started. No partial batches.

---

## 8. Context discipline and stateless orchestration

Two related rules that, together, prevent the orchestrator (this skill,
a runner, or any future coordinator) from collapsing under its own
weight as a batch grows. Both are OWNED by /cards because the planner
sets the contract everyone downstream honors.

### 8a. Executor context

When the runner spawns an executor agent for a card, the executor's
prompt context MUST contain only the card body, access to the project
repo, and the `trace_id`. The runner MUST NOT forward the batch
manifest, sibling cards (even direct dependencies), the planning
conversation, or planner-reviewer disagreements.

If the executor needs information from a dependency, that information
is in committed code on the dependency's branch (readable normally) or
summarized in the card's Pointers section. Quadratic context explosion
is the canonical multi-agent failure mode; this constraint prevents it
at the spawn site.

The planner is responsible for writing self-contained cards.

### 8b. Cards are state, the orchestrator is stateless

> The card is the durable unit of state. The orchestrator (this skill,
> a runner, or any future coordinator) MUST NOT hold task state in its
> own context window. To answer "what is the state of card X" the
> orchestrator reads the card.

This keeps orchestrator context minimal, makes the system survivable
across orchestrator restarts, and enables future deployment where the
orchestrator is a local or resource-constrained process that simply
cannot afford to remember an entire fleet's worth of in-flight work.

The cost is one filesystem read per query. The benefit is a coordinator
that cannot bloat itself into uselessness mid-batch.

Practical consequences for /cards itself: the planner does not keep a
session of "what cards exist where"; on each invocation it reads
`C:\dev\todo\` to see ground truth. There is no in-memory cache. The
runner has the same constraint: claim, work, write, release the card,
forget. The card's frontmatter and body carry everything needed for
the next pass to pick up.

---

## 9. `/cards validate` subcommand

Scans `C:\dev\todo\` and reports cards whose `status:` frontmatter
field disagrees with their subfolder location. The subfolder is
canonical; the field is convenience. They should agree.

Report format: list each divergent card with `<id>: in <subfolder>,
status field says <status>`. Exit 0 if no divergence, exit 1 if any
found.

`/cards validate` does NOT auto-repair. The right repair depends on
why the divergence happened (executor crash mid-move? manual mv? a
runner with a stale read?). Surface and stop.

---

## 10. Token discipline

`/cards` is a planning tool, not a debate club. Concretely:

- Two rounds of planner-reviewer back-and-forth, max. After that, log
  disagreements and ship.
- One dry-run summary. If the user asks for revision, run up to three
  revision cycles, then surface the tie.
- The skill itself reads only the necessary protocol files, the
  project config, and the user's input. It does not read the project's
  full repo at planning time; that's the executor's job.

When in doubt: prefer fewer rounds, log the uncertainty in the
manifest, let Drew decide.

---

## 11. AC immutability and the standup amendment pattern

The `acceptance_checks:` block of a card is immutable after the card
is written. The executor agent MUST NOT edit it directly. This is
enforced in `RUNNER_CONTRACT.md` (executor protocol) and recapped in
section 2 above.

The escape valve, because reality sometimes invalidates a planner's
assumption, is the standup amendment pattern:

1. The executor adds a `change_request:` block to the card body
   explaining the proposed amendment and the reasoning. Suggested
   shape:

   ```yaml
   change_request:
     proposed_at: 2026-05-17T14:32:00Z
     proposed_by: executor-agent-id-or-trace
     target_check: "the description string of the AC item, or its index"
     proposed_change: "what the executor wants changed"
     reasoning: "what they learned that invalidates the original"
     evidence: "command output, file path, log excerpt"
   ```

2. The card moves from `active/` to `amendments/` and its `status`
   field is set to `awaiting_amendment_review`. The runner does the
   move; the executor signals it by writing the `change_request`
   block and then exiting cleanly.

3. The reviewer (Drew, and / or a sibling reviewer agent) evaluates
   the request in a standup. This is a deliberately human-touched
   gate: the planner thought one thing, the executor learned another,
   and the call between them is not delegated to either side
   unilaterally.

4. Outcomes:
   - **Approved.** The AC item is amended in place. Provenance fields
     are attached to the changed item (`amended_at`, `amended_by`,
     `amendment_reason`, plus the full `original:` block of the
     pre-amendment item). The card moves back to `active/` and the
     executor finishes against the amended AC. The original is
     retained so the change is auditable forever.
   - **Denied.** The executor finishes against the original AC, or
     the card moves to `blocked/` if the original truly cannot be
     satisfied.

Amended item shape inside `acceptance_checks:`:

```yaml
acceptance_checks:
  - description: "Post-amendment description"
    check: { ... amended check spec ... }
    amended_at: 2026-05-17T15:10:00Z
    amended_by: drew
    amendment_reason: "Original test assumed in-process bucket; redis
      bucket is the only correct shape after b001-02 revision."
    original:
      description: "Pre-amendment description"
      check: { ... pre-amendment check spec ... }
```

This pattern preserves the executor's voice (they raised the issue)
without giving them the keys to the contract (they cannot unilaterally
relax it). Cost is one human review per amendment. Benefit is a
contract that cannot be silently rewritten mid-run.

---

## 12. Human-in-the-loop principle

The /cards system is not designed for a world where Drew is absent.
Drew is a permanent collaborator who participates in standups,
retros, and amendment reviews. The system is built around that fact,
not around a future where the human is optimized out.

Concretely this shapes:

- **AC amendments** (section 11) require a human reviewer in the
  approval path. A sibling reviewer agent may participate, but the
  approval call is human-touched by default.
- **Sprint close** (Future Work, sprint scheduler entry in
  `README.md`) surfaces every unfinished card as a forced decision
  for the human, not as a silent rollover.
- **High-stakes merge gates** (tier 5 / 6) already route to Drew per
  `RUNNER_CONTRACT.md`. That stays.
- **Planner-reviewer disagreements** that don't converge in two
  rounds are surfaced to Drew, not auto-resolved.

Where the system can absorb human absence (the runner survives
restarts; cards are durable state; orphan reclaim handles executor
crashes) it does. Where the system requires human judgment (contract
amendments, sprint scoping, high-stakes review) it asks. The
distinction is deliberate, not a transitional state.

---

## 13. Cold-read verification

Every card that leaves `active/` for `done/` is, by default, signed off
by a cold-read verifier agent before the move. The verifier is a fresh
agent invocation: no executor conversation history, no planner context,
no sibling chatter. It receives only the card body, the executor's
recorded outputs (`actual_tokens`, completion notes), and access to the
project repo. It runs the `acceptance_checks:` block from scratch and
either signs the card off or sends it back.

This is owned by the runner; the skill commits to the schema and the
contract. The full executor / runner / verifier protocol lives in
`RUNNER_CONTRACT.md` under "Cold-read verification". This section
documents the planner-side commitments and the user-visible behavior.

### What the skill commits to

The card schema reserves three fields the verifier owns:

- `verified_at` -- ISO 8601 UTC timestamp the verifier completed. Null
  while no verification has run.
- `verified_by` -- agent id or label. Null while no verification has
  run.
- `verifier_skipped_reason` -- nullable string. When the verifier was
  legitimately skipped (see below), the reason is recorded here. When
  the verifier ran, this field is null. Never both populated.

These fields are appended to `templates/card.md`. They are write-once
per terminal transition: the runner sets them when moving the card to
`done/`, and any future re-verification (manual override) writes a new
trio of values, preserving the prior values as a stacked audit trail
(see RUNNER_CONTRACT.md for the stack shape).

### When the verifier may be skipped

The verifier MAY be skipped only when the executor's run satisfies all
of the following:

1. The executor did not escalate via cascade-on-confidence during the
   card (i.e., `cascade_history` is empty). A card whose runtime had to
   climb tiers is a card whose initial confidence was wrong, which is
   the exact situation a cold read is for.
2. The executor's final self-reported confidence is "very high." The
   default threshold is `>0.9` on a 0-1 scale; a project may tighten
   but not relax that floor via `project_config.yaml`.
3. Every acceptance check passed on first run, with no retry attempts
   recorded in the runner's per-card log.

When skipped, the runner MUST populate `verifier_skipped_reason` with
a short human-readable string. The default value is
`"high-confidence cascade-clean run"`. Cards in `done/` with a null
`verifier_skipped_reason` AND null `verified_at` are a contract
violation; the validator (`/cards validate`) flags them.

### Manual override

The runner contract defines a `run_verifier(card_id)` entry point so
external triggers (dashboard "Run Cold Read Now" button, a CLI, a
scheduled re-audit) can force a verification pass on any card in any
state. The result shape is fixed: `{result: pass | fail | error,
reasons: [...], at: timestamp, agent_id: ...}`. The full contract for
the entry point lives in RUNNER_CONTRACT.md.

A manual override never deletes prior verifier state; it appends. This
makes "we re-verified this card 30 days later when an audit asked"
auditable forever.

---

## Reference files in this skill folder

- `README.md` -- human-facing spec
- `tier_map_claude.yaml` -- tier 1-6 to claude model + thinking
- `tier_pricing.yaml` -- per-model token prices (USD per 1M tokens),
  used to derive USD figures from token counts at display / cap-check
  time. Cards never store USD; tokens are immutable, USD is derived.
- `RUNNER_CONTRACT.md` -- contract surface for the runner
- `templates/card.md` -- card frontmatter + body template
- `templates/batch_manifest.yaml` -- manifest template
- `templates/project_config.yaml` -- per-project config template
- `examples/b001-03-add-rate-limit-middleware.md` -- example card
- `tests/atomic_rename_test.ps1` -- verifies that moving a file
  between sibling subfolders on NTFS is atomic under concurrent
  contention (named for the underlying syscall it exercises)
