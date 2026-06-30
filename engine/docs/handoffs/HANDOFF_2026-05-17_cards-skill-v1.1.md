# Handoff: /cards skill v1.1 (consultant fixes)

**Date:** 2026-05-17
**Session:** Cowork, focused v1.1 follow-up after Pairing B consultation
**Branch:** `feature/cards-v1.1-consultant-fixes` (off `main`)
**Predecessor handoff:** `HANDOFF_2026-05-16_cards-skill-v1.md`

---

## What this session did

A single focused pass folding six consultant-approved fixes into the
/cards skill. No scope creep. Everything below is in the branch
`feature/cards-v1.1-consultant-fixes` off `main`.

### 1. AC immutability + standup amendment pattern

- SKILL.md section 2 now states the immutability rule (executor MUST
  NOT edit `acceptance_checks:` after card creation) and points at the
  amendment mechanism.
- New SKILL.md section 11 ("AC immutability and the standup amendment
  pattern") documents the full executor / reviewer protocol, the
  `change_request:` block shape, the four outcomes, and the
  amended-item provenance shape (`amended_at`, `amended_by`,
  `amendment_reason`, full `original:` block retained).
- RUNNER_CONTRACT.md gained an "AC amendment protocol (executor and
  runner)" section. Executor side, runner side, reviewer side are
  spelled out.
- New card status `awaiting_amendment_review` and new subfolder
  `amendments/` added to:
  - SKILL.md (via section 11)
  - RUNNER_CONTRACT.md (directory invariants, status transitions
    diagram, status field enum, "What the skill commits to")
  - skills/cards/README.md (intro paragraph)
  - C:\dev\todo\README.md (subfolder list)
- The `C:\dev\todo\amendments\` directory was created on this device.
  C:\dev\todo\ is not under git so no commit is required for the
  directory itself; remote devices need to run a one-line
  `New-Item -ItemType Directory -Path C:\dev\todo\amendments` to
  match.

### 2. Done definition tightened at planning time

- SKILL.md section 2 now requires the planner to produce a
  machine-verifiable Done definition for every card. AC items must be
  runnable assertions; prose-only items are not allowed.
- SKILL.md section 4 (Validation) gained a 5th check, "AC machinability
  check," that enforces this at write time. Exception: tier 5 / 6
  cards may carry one item marked `subjective: true` per card because
  the merge gate already routes through a human.

### 3. Worktree isolation and cross-contamination defense

- RUNNER_CONTRACT.md gained a new section
  "Worktree isolation and cross-contamination defense" between
  "Branch and worktree protocol" and "Merge gates". Six numbered
  enforcements:
  1. Per-worktree clean env block (no credential vars shared across
     siblings by default).
  2. Per-worktree dependency caches (`node_modules/`, `__pycache__/`,
     `.venv/`, etc.; no symlinks to a shared install).
  3. Per-worktree git config via `git config --worktree`.
  4. Pre-flight clean-state check as the executor's first action.
  5. Mid-execution log / output isolation under
     `C:\dev\todo\_runs\<trace_id>\`, readable only after terminal
     transition.
  6. Serialized worktree creation via `C:\dev\todo\.runner.lock`
     global mutex (the existing #34645 mitigation, now framed as one
     of six required defenses).

### 4. Multi-agent planning future-work note

- SKILL.md section 2 now ends with a "Future optimization note (not
  committed for v1.1)" describing the staged tier variant (sonnet
  decomposition + opus AC / tier + adversarial review) as a token-
  efficiency lever to be explored once `/cards stats` produces
  estimated-vs-actual data. Flagged, not committed.

### 5. Auto-carry with friction in sprint scheduler Future Work

- README.md "Future work" entry for the sprint scheduler now spells
  out that sprint close forces a human decision on every unfinished
  card (re-queue, deprioritize, split, drop, escalate). No silent
  rollover. Linear-style cycle close.

### 6. Human-in-the-loop principle

- New SKILL.md section 12 ("Human-in-the-loop principle") makes
  explicit that Drew is a permanent collaborator. The system is built
  around that, not around a future where the human is optimized out.
  Lists where this shapes behavior: amendments, sprint close,
  high-stakes merge gates, planner-reviewer disagreements.

---

## What is intentionally NOT in this pass

Per the no-scope-creep constraint:

- `templates/card.md` was NOT updated. Its single-line `status` enum
  comment now lags the spec by one value (`awaiting_amendment_review`
  is missing from the comment). The frontmatter schema in the template
  is example data, not the canonical spec; the canonical lives in
  RUNNER_CONTRACT.md. A one-line edit to bring the template comment
  back in sync is a clean v1.2 micro-change.
- `templates/batch_manifest.yaml` was NOT touched. The planner-
  disagreement and parallel-hazard blocks already exist there; no
  amendment-related schema needed in the manifest itself.
- No new validation tests were added for the AC machinability check.
  The check is documented in SKILL.md section 4; an executable test
  belongs in `skills/cards/tests/` and is a separate small task.
- The atomic-rename verifier (`tests/atomic_rename_test.ps1`) was
  NOT executed on this device. The v1 handoff already flagged this
  as a known gap; v1.1 inherits the same gap. The new
  `amendments/` subfolder is functionally equivalent to the existing
  subfolders for atomicity purposes (it's just another sibling
  directory), so the existing test, when run, covers the new path
  too.

---

## What is currently broken or incomplete

Nothing known. All file edits applied successfully. Branch is live.
Commit status: see "Commit and PR state" below.

---

## Commit and PR state

```
Branch:  feature/cards-v1.1-consultant-fixes
HEAD:    8afd68a cards: v1.1 consultant fixes (AC immutability, worktree isolation, HITL)
Pushed:  yes (origin/feature/cards-v1.1-consultant-fixes)
PR:      https://github.com/Ginkobaloba/dev-meta/pull/1
```

---

## What the next session should do first

1. Read `C:\dev\SESSION_PROTOCOL.md`.
2. Read `C:\dev\_meta\CLAUDE.md`.
3. Read this file and its predecessor
   (`HANDOFF_2026-05-16_cards-skill-v1.md`).
4. Run `vstart` from `C:\dev\_meta\`.
5. If the PR from this session is open, decide whether to merge or
   request changes.
6. Pick the next chunk:
   - The runner itself (still not built; biggest piece of remaining
     work; contract surface is RUNNER_CONTRACT.md).
   - A one-line `templates/card.md` enum-comment update for parity
     with the v1.1 status enum (trivial).
   - An executable test for the AC machinability check
     (skills/cards/tests/, ~30 min).
   - Run `tests/atomic_rename_test.ps1` on this device and record the
     result in the v1 handoff (still unrun on any device).

---

## Open questions for Drew

- The `awaiting_amendment_review` field value pairs with the
  `amendments/` subfolder. Naming asymmetry is intentional (short
  folder name, explicit field name). If you'd rather have them match
  exactly, the cleaner change is to rename the folder to
  `awaiting-amendment-review/` (kebab-case per
  NAMING_CONVENTIONS.md). Flag if you want the rename.
- The AC machinability check has an exception for tier 5 / 6 cards
  carrying one subjective item marked `subjective: true`. That
  `subjective:` flag is new schema not currently in
  `templates/card.md`. If you want it documented in the template too,
  that's the v1.2 micro-change above.
- Sibling reviewer agents as part of amendment approval: SKILL.md
  section 11 says human-touched by default but allows sibling reviewer
  agents to participate. RUNNER_CONTRACT.md says the runner can
  delegate approval to a reviewer agent if the project config
  explicitly says so. No project config schema field exists yet for
  that delegation. Add to `templates/project_config.yaml` when you
  decide on the mechanism.

---

## Pointers

Files modified this session:

```
skills/cards/SKILL.md            (~+135 lines)
skills/cards/RUNNER_CONTRACT.md  (~+115 lines)
skills/cards/README.md           (~+12 lines)
todo/README.md                   (~+3 lines, in C:\dev\todo\)
```

Files created this session:

```
docs/handoffs/HANDOFF_2026-05-17_cards-skill-v1.1.md  (this file)
C:\dev\todo\amendments\          (empty directory, not git-tracked)
```

Related Pairing B consultation notes (Drew's source for these six
fixes): not in this repo. Drew has them locally.

---

## Atomic-rename test result

Still **Not yet run**. Same status as the v1 handoff. The new
`amendments/` subfolder is structurally identical to the existing
status subfolders, so the existing test covers it once run.

```
Device: <hostname>
Date:   YYYY-MM-DD
Script: skills\cards\tests\atomic_rename_test.ps1
Result: PASS | FAIL
Notes:  ...
```

---

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md`
in this project, then this file (the most recent handoff), then run
`C:\dev\_scripts\session-start.ps1` (alias `vstart`).

When the next session ends, write a new handoff doc using
`docs/handoffs/template.md` as the seed, and run `vend`.
