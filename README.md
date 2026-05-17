# /cards

Decompose a user story or pasted discussion into independent, claimable
cards that a fleet of agents can run in parallel.

The skill is the planner. It writes cards to `C:\dev\todo\backlog\` and a
batch manifest to `C:\dev\todo\_batches\`. A separate runner (out of scope
for this skill) polls `backlog\`, spawns executor agents, and moves cards
through `active\` -> `done\` or `blocked\`. Cards whose executor proposes
an AC amendment (see SKILL.md section 11) pass through `amendments\` for
human review before resuming.

---

## When to use it

Invoke `/cards <user story or pasted discussion>` when you have a chunk of
work that:

- can be broken into roughly five or more pieces
- has real parallelism (the pieces don't all serialize on one file)
- benefits from per-piece model selection (some cheap, some expensive)

If the work is one piece, two pieces, or a chain where every step depends
on the previous one, `/cards` will say so and stop. No point manufacturing
ceremony.

---

## What it produces

Per card, a markdown file with YAML frontmatter, written to
`C:\dev\todo\backlog\<id>.md`. Frontmatter carries the metadata
(tier, model, dependencies, branch, merge gating). Body has five sections
plus a sixth that the runner appends on completion.

Per batch, a manifest at `C:\dev\todo\_batches\<batch>-manifest.yaml` with
the planning summary, the cards in the batch, dependency edges, and any
planner-reviewer disagreements logged verbatim.

---

## How sizing works

Two axes, independent:

- **Stakes** (low / medium / high) -- blast radius if the work goes wrong
- **Difficulty** (shallow / deep) -- how much thinking the work needs

The two axes combine into a tier (1-6). Tier maps to a Claude model and
extended-thinking setting via `tier_map_claude.yaml`. Stakes also sets the
`model_floor` (haiku / sonnet / opus) which is the hard family-level
constraint, and `pin_required` (true when stakes are high). Difficulty
sets `thinking_depth`.

| Stakes \ Difficulty | shallow | deep |
|---|---|---|
| low | tier 1 (haiku, no thinking) | tier 2 (haiku, thinking) |
| medium | tier 3 (sonnet, no thinking) | tier 4 (sonnet, thinking) |
| high | tier 5 (opus, no thinking) | tier 6 (opus, thinking) |

Provider swap is a single file edit: add `tier_map_<provider>.yaml`,
re-point the skill at it. Cards are LLM-agnostic at the schema layer.

---

## Acceptance criteria are machine-checkable

A card's "Acceptance criteria" body section contains a fenced YAML block
named `acceptance_checks:`. Each item is `{description, check}` where
`check` is one of: `shell` (cmd, exit 0 = pass), `file_exists`,
`file_absent`, `grep_match`, `grep_absent`, `http_status`.

The executor runs every check and records pass/fail per item in the
runner-appended "Completion notes." A card only moves to `done/` if
every check returns pass.

Prose-evaluation amplifies errors at batch scale (a single ambiguous
"works correctly" criterion compounds into many fuzzy verdicts), so
binary checks are the default. Cards that genuinely need subjective
review should be tier 5 or 6 -- the high-stakes path already routes
through human approval, so subjective AC lands where eyes are present.

---

## Orphan reclaim

Cards in `active/` carry a `last_heartbeat` field that the executor
updates periodically. If a card sits in `active/` with a heartbeat older
than `orphan_timeout_minutes` (default 120, configurable per project),
the next runner pass moves it back to `backlog/` and clears
`claimed_by`, `started_at`, and `last_heartbeat`.

This isn't the runner's job to invent later; the metadata for it lives
in the card schema from day one. Retrofitting concurrency safety after a
fleet of executors is already in flight is the kind of pain that's
cheap to avoid now.

---

## Story drift detection

Every card carries `story_hash` -- sha256 of the source story text at
plan time. The full source text is preserved in the batch manifest under
`source.text`.

When a project sets `story_source_path` in its config, the runner
re-hashes that file on every claim and compares against the card's
`story_hash`. Mismatch flags the card for re-triage rather than executing
against drifted intent. Without `story_source_path`, the hash is just a
fingerprint for forensics if a re-plan happens.

This catches the silent failure mode where a user story or RFC is
edited after planning, leaving cards in the backlog that were correct
for the old version of the story and wrong for the new one.

---

## Git workflow

Per-card feature branches by default, named `card/<id>`. Merge gating is
tier-aware:

- low stakes (tier 1, 2): auto-merge if lint passes, tests pass, no conflicts
- medium stakes (tier 3, 4): auto-merge after a sibling-agent review pass
- high stakes (tier 5, 6): PR opens, Drew approves manually

A card moves to `done/` only when `merge_status: merged`. Finished work
that hasn't merged (conflict, review pending) lives in `blocked/` until
unblocked.

**Lean mode** is a per-project opt-in. In lean mode every card in a batch
commits to one shared branch `cards/<batch_id>`, and there's one PR per
batch instead of one per card. Configure in
`<project>\.cards-config.yaml`. Defaults to full mode.

---

## Planning agents

Default two agents, both opus, both model-pinned and directly invoked:

- **Planner** -- proposes the decomposition
- **Reviewer** -- adversarial, looks for missing dependencies, ambiguous
  scope, undersized or oversized cards, parallel cards that secretly
  fight over the same file

If the input exceeds the deep-mode threshold (configurable, default ~3000
tokens) or `/cards --deep` is passed, the skill scales to three:

- **Decomposer** -- raw breakdown
- **Estimator** -- sizes each card on the two axes
- **Reviewer** -- adversarial, same role

Disagreements between planning agents are logged in the batch manifest
under `planner_disagreements:`. Not silently averaged.

---

## The five invocation steps

1. **Resolve context.** Read `C:\dev\NAMING_CONVENTIONS.md` (fall back to
   `C:\dev\_meta\NAMING_CONVENTIONS.md`), `C:\dev\SESSION_PROTOCOL.md`,
   the target project path, and the project-level config if present.
2. **Planning pass.** Run two or three planning agents per the rules above.
3. **Tier assignment.** Per card, set stakes and difficulty, derive tier,
   look up Claude model + extended_thinking in `tier_map_claude.yaml`,
   derive `model_floor` and `pin_required` from stakes.
4. **Dry-run summary.** Show card count, points histogram, dependency edges,
   count of immediately-claimable cards. Pause for explicit approval.
5. **Write.** On approval, write card files to `C:\dev\todo\backlog\` and
   the manifest to `C:\dev\todo\_batches\`. Commit if the destination is
   under git.

If step 4 returns fewer than 5 cards or zero parallelism, the skill says
so and exits without writing.

---

## Planner-vs-reality feedback loop

Every card carries four numeric fields that close the loop between what
the planner thought the work would take and what the work actually
took:

| Field | Set by | What it captures |
|---|---|---|
| `estimated_tokens` | planner | total token budget across all model calls |
| `actual_tokens` | executor | sum of input + output tokens consumed |
| `estimated_duration_minutes` | planner | wall-clock estimate |
| `actual_duration_minutes` | derived | `finished_at` minus `started_at` |

These exist for one purpose: feedback. The planner makes an estimate.
The executor records reality. The delta tells future planners how to
size tier rubrics. Over enough cards, the system learns where its
tier reads are systematically optimistic or pessimistic and the
planner prompts can be adjusted from data instead of vibes.

A `/cards stats` subcommand to aggregate these into accuracy-over-time
reporting is enumerated in Future Work; not in v1.

---

## Tokens immutable, USD derived

Cards record `estimated_tokens` and `actual_tokens` but do NOT record
USD. USD is a derived view, computed at display-time or
cap-check-time by multiplying token counts against the current
`tier_pricing.yaml`:

```
actual_cost_usd = (input_tokens  * input_price_per_million  / 1_000_000)
                + (output_tokens * output_price_per_million / 1_000_000)
```

When Anthropic (or another provider) changes prices, edit
`tier_pricing.yaml`. Cards on disk stay correct because tokens are
immutable facts; the USD column on any report just re-renders at the
new rate. `cost_cap_usd` (the planner-set budget ceiling) is the only
USD field that lives on cards, because budgets are how humans think
about spend; the runner converts on demand using the price table to
decide whether to halt.

Trade-off: historical USD reports drift if you compare them across
price changes. If you need audit-grade pinned pricing for a specific
batch, copy the price block into the batch manifest at plan time. Not
automated in v1; enumerated as future work.

---

## Pre-approval, cost caps, and tracing

Three additional frontmatter fields support safer parallel execution:

- `requires_pre_approval` -- defaults true for high-stakes cards.
  Broader than `pin_required`: it gates whether the runner is allowed
  to start the card at all, not just which merge path runs. Use this
  for cards where even the planning was risky enough you want eyes
  on it before anything runs.
- `cost_cap_usd` -- optional ceiling on cumulative spend (planning
  agents, executor, sibling-review). Runner halts the card if tracked
  spend crosses the cap. `null` = no cap. Planner can set this
  defensively on tiers that historically blow budget.
- `trace_id` -- uuid generated per card at creation, propagated to
  every sub-agent call and log event for cross-system correlation.
  When the executor's logs and the runner's logs and a downstream
  observability tool all share `trace_id`, postmortems are tractable.

---

## `/cards validate`

A subcommand that scans `C:\dev\todo\` and reports cards whose
`status:` frontmatter field disagrees with their subfolder. This is the
sanity check against dual-source-of-truth drift (subfolder is canonical,
field is convenience; they should agree).

Run it manually after any manual file moves, or schedule it. It only
reports; it does not auto-repair, because the right repair depends on
why the divergence happened.

---

## Future work (deferred, not built in v1)

A list of things considered and explicitly out of scope for v1. These
are notes for future-Drew, not promises.

- `/cards stats` subcommand. Aggregates the estimated vs actual
  token / duration fields across a batch (or across history) and
  reports where the planner's tier rubrics are systematically off.
  USD aggregates are computed against the current `tier_pricing.yaml`
  at report time, never against any per-card stored USD (which does
  not exist). Feeds back into rubric tuning.
- Pinned pricing in batch manifests. Copy the relevant block from
  `tier_pricing.yaml` into the batch manifest at plan time so audit
  reports can reconstruct historical USD figures even after prices
  change. Currently the price table is global and mutable.
- Sprint scheduler. A UI / dashboard layer on top of /cards that
  defines a "sprint" as a target set of cards expected to complete in
  one ~5-hour usage window. Aggregates the token and duration metrics
  into per-sprint retros (estimated vs actual, what got cut, what
  blocked). Lets the user schedule future sprints based on historical
  accuracy. At sprint close, every unfinished card surfaces as a
  forced human decision (re-queue, deprioritize, split, drop,
  escalate). No silent rollover. This is the Linear-style cycle close
  behavior; it is the only way to keep the planner-vs-reality loop
  honest, because silent auto-carry hides the same misestimation
  every sprint until the backlog is unsustainable. Natural product
  extension: package /cards plus the scheduler plus a local
  orchestrator that doesn't have token caps as a single tool for
  solo-with-agents agile.
- Dead-letter queue for cards that fail repeatedly.
- Dynamic backlog reprioritization (re-tier in flight).
- Cryptographic `claimed_by` (signed claims to prevent runner spoofing
  in multi-host setups).
- Saga compensation fields (rollback steps as first-class card data).
- Post-hoc cold-read verifier agent (third-party check against
  completed work).
- RL-trained sequencer to optimize claim order beyond simple FIFO.
- Semantic dedup across batches (detect when two batches plan the
  same card under different ids).
- A2A-SAGA rollback for cross-card state changes.
- Planning-prompt fingerprinting (hash of the planner's full prompt +
  context, separate from `story_hash`, for full reproducibility of
  the plan itself).

If any of these become urgent, add a card. They are not blocking v1.

---

## What the runner does (out of scope for this skill)

See `RUNNER_CONTRACT.md` for the full contract. Short version: the runner
watches `backlog\`, claims cards whose dependencies are satisfied,
spawns executor agents at the card's pinned model, drives them through
the per-card branch + merge gate, and moves cards into `active\` ->
`done\` or `blocked\` based on outcome.

---

## Token discipline

`/cards` is a planning tool, not a debate club. The planner-reviewer pass
should be one round of critique and one round of revision. Beyond that,
the cost of additional rounds exceeds the marginal information. If the
planner and reviewer can't converge in two rounds, log the disagreement
and ship cards anyway. Drew sees the disagreement in the manifest and
decides.
