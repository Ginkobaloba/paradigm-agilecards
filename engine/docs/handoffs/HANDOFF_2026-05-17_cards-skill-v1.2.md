# Handoff: /cards skill v1.2 (cold-read verifier + cascade-on-confidence)

**Date:** 2026-05-17
**Session:** Cowork, focused v1.2 follow-up after repo migration
**Branch:** `feature/cards-v1.2-cold-read-and-cascade` (off `main`)
**Predecessor handoffs:**
- `HANDOFF_2026-05-16_cards-skill-v1.md`
- `HANDOFF_2026-05-17_cards-skill-v1.1.md`
- `HANDOFF_2026-05-17_repo-migration.md`

---

## What this session did

A single focused pass folding two v1.2 contract additions and three
v1.1 carry-over micro-fixes into the /cards skill, against the new
`agile-cards` repo (post-migration from `_meta`).

### 1. Cold-read verifier (required in v1.2)

- New SKILL.md section 13 ("Cold-read verification") spells out the
  planner-side commitments: the schema fields the runner owns
  (`verified_at`, `verified_by`, `verifier_skipped_reason`), the
  conditions under which the verifier MAY be skipped (cascade-clean
  run + executor self-confidence at or above the project threshold +
  every check passing on first run), the manual-override entry point,
  and the "no null/null in done/ is allowed" contract violation rule
  enforced by `/cards validate`.
- New RUNNER_CONTRACT.md section "Cold-read verification" carries the
  full executor / verifier / runner protocol: result shape
  (`pass | fail | error`), retry behavior on verifier error, the
  `run_verifier(card_id) -> verifier_result` external entry point
  (the contract behind the dashboard "Run Cold Read Now" button),
  and the `verifier_history` audit list for manual overrides.
- Card state-transition diagram in RUNNER_CONTRACT.md updated to show
  the verifier as the gate between "executor marks finish" and a move
  to `done/`. Verifier fail returns the card to `active/` with
  `verifier_notes` written into the body.
- Brief pointer in SKILL.md section 2 explaining cascade is a runtime
  concern owned by the runner, not a planning concern.

### 2. Cascade-on-confidence routing (required in v1.2)

- New RUNNER_CONTRACT.md section "Cascade-on-confidence routing" with
  the executor protocol, confidence-probe shape, escalation rule
  (escalate one tier when combined confidence falls below
  `cascade_escalation_threshold`, default 0.6), the hard cap of two
  escalations per card, and the append-only `cascade_history`
  frontmatter shape `[{from_tier, to_tier, reason,
  confidence_at_escalation, at}, ...]`.
- Interactions documented explicitly: cascade history disqualifies
  verifier-skip; cost cap is re-evaluated immediately after each
  escalation against the projected remaining spend; pinning still
  forces the merge gate but does not block escalation; `model_floor`
  is respected (escalation only climbs).
- "What the skill commits to" list in RUNNER_CONTRACT.md updated with
  the new frontmatter field names and shapes (`verified_at`,
  `verified_by`, `verifier_skipped_reason`, `cascade_history`,
  `subjective: true`).

### 3. v1.1 follow-ups

All three open questions from the v1.1 handoff resolved:

- `templates/card.md`: status enum comment updated from
  `backlog, active, done, blocked` to
  `backlog, active, awaiting_amendment_review, done, blocked`, with
  a one-line pointer to the asymmetry rule (the field value pairs
  with the `amendments/` subfolder).
- `templates/card.md`: `subjective: true` documented as an optional
  per-item flag inside the `acceptance_checks:` block, with a
  commented-out example showing the tier-5/6-only constraint.
- `templates/project_config.yaml`: new `reviewer_delegation` field
  (boolean, default `false`) controls whether a sibling reviewer agent
  may approve a `change_request` without a human standup pass.

### 4. New / updated card frontmatter

`templates/card.md` gained five frontmatter additions in the runner-
owned block (after `merge_status`):

- `verified_at: null`
- `verified_by: null`
- `verifier_skipped_reason: null`
- `cascade_history: []`

Plus the inline subjective-flag documentation in the
`acceptance_checks:` example block.

### 5. New project config knobs

`templates/project_config.yaml` gained three knobs (with documented
defaults and constraints):

- `reviewer_delegation: false`
- `verifier_skip_confidence_threshold: 0.9` (project MAY tighten,
  MUST NOT relax below 0.9)
- `cascade_escalation_threshold: 0.6`
- `cascade_max_escalations: 2` (v1.2 contract caps at 2; set 0 to opt
  out of cascade entirely)

---

## What is intentionally NOT in this pass

Per the single-focused-pass constraint:

- The runner itself remains unbuilt. The two new sections in
  RUNNER_CONTRACT.md describe what the runner MUST do once it exists;
  no executable runner code is in this branch.
- The dashboard "Run Cold Read Now" button is not wired up in this
  pass. The contract for the button (the `run_verifier(card_id)`
  entry point) is now spec'd in RUNNER_CONTRACT.md; the
  `dashboard/v0/index.html` from the prior session does not yet call
  it.
- No new tests. The cold-read verifier and cascade routing both belong
  in `tests/` as integration tests once a runner exists to exercise
  them. The AC machinability check tests flagged in v1.1 are still
  open.
- `examples/b001-03-add-rate-limit-middleware.md` was not regenerated
  against the new card schema. It is now one schema version behind
  (missing `verified_at`, `verified_by`, `verifier_skipped_reason`,
  `cascade_history`). A clean regeneration is a trivial v1.3
  micro-task once a planner agent re-runs it.

---

## Repo migration state at the start of this session

When this session began, the `agile-cards` repo at
`C:\dev\agile-cards` was in a partially-settled state:

- The directory rename from `agile_cards` to `agile-cards` had
  completed.
- The most recent commits (PolyForm license, migration handoff
  update, dashboard v0) landed within minutes of session start.
- `.git/refs/remotes/origin/main` existed at the latest commit hash,
  but `.git/config` had no `[remote "origin"]` URL section. The
  remote URL was not configured locally.
- A phantom `.git/index.lock` zero-byte file appeared in `stat` but
  not in `ls`. Linux/Windows mount cache inconsistency, not a real
  lock; git read operations succeeded normally.
- Three files had uncommitted working-copy edits unrelated to this
  task (LICENSE, README.md, the migration handoff). They were left
  untouched in this branch; the parallel migration task owns them.

Per the user's instruction ("commit locally to whatever name is
current and adjust"), this session committed locally on the new repo
name without configuring a remote URL. The push and PR are open
follow-ups; see "What the next session should do first" below.

`core.autocrlf=input` was set locally to prevent CRLF noise from
polluting commits made in this session. Only files explicitly edited
by this session entered the commit.

---

## Commit and PR state

```
Branch:  feature/cards-v1.2-cold-read-and-cascade
HEAD:    (set after commit, see git log)
Pushed:  no (remote URL not yet configured in .git/config; see below)
PR:      not yet opened
```

The intended remote URL per the migration handoff is
`https://github.com/Ginkobaloba/agile-cards`. Once the migration task
finishes wiring the remote, `git push -u origin
feature/cards-v1.2-cold-read-and-cascade` and `gh pr create` will
work as expected.

---

## What the next session should do first

1. Verify the migration is fully settled: `git remote -v` should
   return the GitHub URL, and the working tree should be clean (no
   CRLF noise on un-touched files).
2. Push this branch: `git push -u origin
   feature/cards-v1.2-cold-read-and-cascade`.
3. Open the PR with title `cards v1.2: cold-read verifier +
   cascade-on-confidence` and a body that points to this handoff doc.
4. Decide whether the v1.1 PR (currently open on the old `dev-meta`
   repo) should be closed in favor of the migrated repo's history.
5. Pick the next chunk:
   - Build the runner (still the biggest remaining piece).
   - Wire the dashboard "Run Cold Read Now" button to the
     `run_verifier(card_id)` entry point (small once the runner
     exists).
   - Regenerate `examples/b001-03-add-rate-limit-middleware.md`
     against the v1.2 card schema.
   - Add executable tests for the AC machinability check (still open
     from v1.1).
   - Run `tests/atomic_rename_test.ps1` on this device and record
     the result.

---

## Open questions for Drew

- Confidence probe combination. The spec says the runner combines a
  model self-report with optional project-defined signals to produce
  one confidence value. Combination rule (min? weighted average?
  geometric mean?) is left to the runner. If you have a preference,
  add it to `project_config.yaml` schema in v1.3.
- Verifier model tier. The spec doesn't pin the verifier to a tier.
  Plausible defaults: same tier as the card after any cascade, or
  always sonnet+ET (cheap second opinion). Flag your preference and
  it goes into RUNNER_CONTRACT.md.
- `cascade_history` retention. Currently append-only across the
  card's entire run (re-claim preserves prior entries). If you'd
  rather see a per-attempt reset with the prior runs moved into a
  `cascade_history_archive:` block, say so.
- Should `verifier_skipped_reason` be a free-form string or an enum
  (e.g., `"high_confidence"`, `"manual_skip"`)? Currently free-form
  string with a default. Tightening to an enum is a v1.3 micro-change.

---

## Pointers

Files modified this session:

```
SKILL.md                          (~+80 lines: cascade pointer in
                                   section 2, new section 13)
RUNNER_CONTRACT.md                (~+225 lines: state transitions
                                   updated, two new sections, commits
                                   list updated)
templates/card.md                 (~+30 lines: status enum comment,
                                   five new frontmatter fields,
                                   subjective-flag example)
templates/project_config.yaml     (~+35 lines: reviewer_delegation,
                                   verifier and cascade knobs)
docs/handoffs/HANDOFF_2026-05-17_cards-skill-v1.2.md  (this file)
```

Files NOT modified this session (but adjacent and worth knowing):

```
README.md                  (working-copy diff is parallel migration
                            task's concern, not this session's)
LICENSE                    (same)
docs/handoffs/HANDOFF_2026-05-17_repo-migration.md  (same)
examples/b001-03-add-rate-limit-middleware.md  (now one schema
                            version behind; regenerate in v1.3)
dashboard/v0/index.html    (needs to call run_verifier(card_id);
                            wire-up in next session)
```

---

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then this file
(the most recent handoff), then its two predecessors in this folder.

When the next session ends, write a new handoff doc in this folder.
