# Handoff: /cards skill v1 shipped

**Date:** 2026-05-16
**Session ended on:** Cowork (overnight v1 sweep)
**HEAD at end:** see `git log -1 --oneline` after vend
**Branch:** `main`

---

## What this session did

- Built the `/cards` planner skill end to end. Skill lives at
  `C:\dev\_meta\skills\cards\` and ships in the dev-meta repo, so it
  propagates to every device via `git pull` (no install.ps1 changes
  needed; the skill home is already inside _meta).
- Initialized the card destination at `C:\dev\todo\` with the five
  status subfolders (`backlog`, `active`, `done`, `blocked`,
  `_batches`) and a seeded `.counter` at `0`. The todo root has a
  README explaining the layout.
- Documented the runner contract in
  `skills/cards/RUNNER_CONTRACT.md`. The runner itself is NOT built
  in this session; that's the next chunk of work.
- Added the planner-vs-reality feedback loop (six estimated_*/actual_*
  frontmatter fields plus README documentation; `/cards stats` and a
  sprint-scheduler layer enumerated in Future Work).
- Added the design principle that cards are the durable unit of state
  and the orchestrator is stateless. Documented in both SKILL.md
  (section 8b) and RUNNER_CONTRACT.md ("Cards are state, the runner
  is stateless").
- Cleaned up terminology: "atomic rename" reframed throughout as
  "atomic file move between subfolders." The filename does not
  change; only the parent directory changes. The PS1 test script
  retains its name (it tests the syscall) but its description now
  reflects what it actually verifies.
- Final additive pass: removed `estimated_cost_usd` and
  `actual_cost_usd` from card frontmatter. USD is a derived view,
  not a durable fact. Added `tier_pricing.yaml` at the skill root
  carrying per-model token prices. Runners convert tokens to USD on
  demand for display and `cost_cap_usd` enforcement. README has a
  new "Tokens immutable, USD derived" section explaining the model.
  Future Work includes a pinned-pricing-in-manifest entry so audit
  reports can survive price changes.
- Wrote a synthetic dry-run walkthrough at
  `skills/cards/tests/synthetic_dry_run_walkthrough.md` showing the
  expected shape of the dry-run summary Drew approves before cards
  are written.
- Wrote an NTFS atomic-rename verifier at
  `skills/cards/tests/atomic_rename_test.ps1`. The script races N
  PowerShell jobs renaming the same file and asserts exactly one
  succeeds. Run this once per device before trusting parallel card
  execution on that machine. If it fails, fall back to Move-Item
  with explicit lock retry (script comments document the fallback).
  **This was NOT executed this session because the session is in a
  Linux sandbox and the script is Windows-only.**

## What is currently broken or incomplete

- **THE COMMIT DID NOT LAND.** The Cowork Linux sandbox could not
  write to `C:\dev\_meta\.git\index.lock` -- repeated `git add` /
  `git commit` attempts failed with "Unable to create index.lock:
  File exists" and the bash side has no permission to unlink the
  lock. The skill files are all on disk under
  `C:\dev\_meta\skills\cards\` and this handoff is at its proper
  path; both are untracked/uncommitted. **The next Windows session
  must commit manually.** Suggested commands:

  ```powershell
  cd C:\dev\_meta
  # Remove any stale lock left over from the Cowork session:
  Remove-Item -Force .git\index.lock -ErrorAction SilentlyContinue
  git add skills docs/handoffs/HANDOFF_2026-05-16_cards-skill-v1.md
  git commit -m "cards: ship /cards skill v1"
  # Then vend handles the push.
  ```

  If `git status` shows the pre-existing `docs/handoffs/HANDOFF_2026-04-28_bootstrap.md`
  as modified, that's a separate pre-existing change from before this
  session and is NOT mine to touch. Decide what to do with it
  independently.

- **The atomic-rename verifier has not been run on any device.** This
  is a known gap. Drew (or any session) on a Windows host should run
  it once and record the result in this handoff or a follow-up.

- **No live python validation was possible this session** due to a
  mount-cache desync between the bash sandbox view and the Windows
  file tool view. The Read-tool view of every file is the canonical
  shape and is correct; the bash view was stale. A follow-up session
  on Windows can run a simple parse-validator against the skill files
  directly.

- **The runner does not exist yet.** RUNNER_CONTRACT.md is the
  contract surface. Building the runner is a separate effort.

- **The skill is not yet wired into a CLI front-end.** Invoking
  `/cards` today loads the skill folder; the planner-pass agent
  spawning is documented in SKILL.md but the spawn code lives in the
  agent harness, not the skill folder.

## What the next session should do first

1. Read `C:\dev\SESSION_PROTOCOL.md`.
2. Read `C:\dev\_meta\CLAUDE.md`.
3. Read this file.
4. Run `vstart` from `C:\dev\_meta\`.
5. **Commit the uncommitted skill folder and this handoff** (see the
   "broken or incomplete" section above for the exact PowerShell
   commands). The Cowork session that built v1 could not commit due
   to a sandbox/lock-file permission issue.
6. Run `C:\dev\_meta\skills\cards\tests\atomic_rename_test.ps1` on this
   device. Record the result in this file (PASS or FAIL plus details).
7. Decide what to build next: either (a) the runner (see
   RUNNER_CONTRACT.md, biggest piece of remaining work) or (b) a
   small parse-validator that re-runs the synthetic test on Windows
   directly, or (c) a real `/cards` invocation against an actual
   small story.

## Open questions for Drew

- Does the lean-mode opt-in shape (per-project `.cards-config.yaml`)
  feel right? It's how project_config.yaml is structured today. If
  not, the schema is in `templates/project_config.yaml`.
- For the runner: should claim be a file lock plus rename, or just
  rename relying on filesystem atomicity? RUNNER_CONTRACT.md says
  rename today; the atomic_rename_test result on each device decides
  whether that holds.
- Is GitHub the merge gate for high-stakes cards, or local-only
  branches reviewed in your editor? README assumes GitHub PRs; the
  runner can do either.

## Pointers

Skill folder layout (all under `C:\dev\_meta\skills\cards\`):

```
SKILL.md                       planner workflow, agent-facing
README.md                      human-facing spec
RUNNER_CONTRACT.md             runner contract surface
tier_map_claude.yaml           tier 1-6 -> claude model + thinking
templates/
  card.md                      card template with all frontmatter fields
  batch_manifest.yaml          batch manifest schema + example
  project_config.yaml          per-project config schema
examples/
  b001-03-add-rate-limit-middleware.md   end-to-end example card
tests/
  atomic_rename_test.ps1       NTFS atomicity verifier (run once per device)
  synthetic_dry_run_walkthrough.md   expected dry-run shape
PHASE1_OVERVIEW.md             deprecated, safe to delete
SKILL.md.outline.md            deprecated, safe to delete
```

Card destination:

```
C:\dev\todo\
  backlog/  active/  done/  blocked/  _batches/.counter
```

Frontmatter fields landed in v1 schema (see `templates/card.md`):

```
id, title, project, status, points, stakes, difficulty,
thinking_depth, model, extended_thinking, model_floor,
pin_required, requires_pre_approval, cost_cap_usd, trace_id,
estimated_tokens, actual_tokens, estimated_duration_minutes,
actual_duration_minutes, sizing_note, depends_on, touches, batch,
story_hash, created, started_at, finished_at, claimed_by,
model_used, last_heartbeat, branch, base_branch, merge_status
```

The four estimated_*/actual_* fields (tokens and durations only) close
the planner-vs-reality feedback loop. USD is NOT stored on cards;
tokens are immutable, USD is derived at display / cap-check time via
`tier_pricing.yaml`. The only USD field on a card is `cost_cap_usd`
(budget ceiling). See README "Planner-vs-reality feedback loop" and
"Tokens immutable, USD derived" sections.

New file in v1: `skills/cards/tier_pricing.yaml` -- per-model token
prices in USD per million tokens. Update when Anthropic changes
prices; cards on disk stay correct. Verification status at v1 is
"unverified-supplied-by-user" -- confirm against the public price
page before trusting it for real budget decisions.

Body sections: Context, Scope, Out of scope, Acceptance criteria
(with machine-checkable `acceptance_checks:` YAML block inside),
Pointers. Runner appends a sixth "Completion notes" section on
terminal transition.

Deferred to future work (in README.md "Future work" section):

- `/cards stats` subcommand (aggregates estimated_*/actual_* fields)
- sprint scheduler / dashboard layer (5-hour usage window planning,
  per-sprint retros, product-extension path)
- dead-letter queue
- dynamic backlog reprioritization
- cryptographic claimed_by (signed claims)
- Saga compensation fields
- post-hoc cold-read verifier agent
- RL-trained sequencer
- semantic dedup across batches
- A2A-SAGA rollback
- planning-prompt fingerprinting

Recent commits: run `git log --oneline -10` after vend.

Related handoffs: `docs/handoffs/HANDOFF_2026-04-28_bootstrap.md`.

---

## Atomic-rename test result

**Not yet run.** Record here on next Windows session:

```
Device: <hostname>
Date:   YYYY-MM-DD
Script: skills\cards\tests\atomic_rename_test.ps1
Result: PASS | FAIL
Notes:  ...
```

---

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in
this project, then this file (the most recent handoff), then run
`C:\dev\_scripts\session-start.ps1` (alias `vstart`).

When the next session ends, write a new handoff doc using
`docs/handoffs/template.md` as the seed, and run `vend`.
