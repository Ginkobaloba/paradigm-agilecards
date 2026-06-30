# Handoff: Paradigm S41 -- Definition of Done codification

Date: 2026-05-25
Branch: `Nexus/lucid-mendeleev-91099f` (worktree at
`C:\Users\Drama\Desktop\Claude\agile-cards\lucid-mendeleev-91099f`,
off `main` at `5a00e74`)
Author: Cowork session (Drew to review)

## What this session did

Single-deliverable session for **sprint S41** of the Paradigm plan
(`C:\dev\PARADIGM_PLAN.md`, Section 5.5).

**Spec (verbatim from the plan, row S41):**

> Write the project-wide DoD: brand-compliance checklist (against the
> Section 3 spec), WCAG AA minimum, Lighthouse thresholds,
> test-coverage bar, the security-review gate for auth changes,
> deploy verification.
>
> Output contract: `DEFINITION_OF_DONE.md`

Produced `DEFINITION_OF_DONE.md` at the repo root, alongside
`SKILL.md` and `RUNNER_CONTRACT.md` (matching the existing convention
of top-level cross-cutting contracts).

## What shipped

### `DEFINITION_OF_DONE.md` (new, root)

Nine sections, structured so a reviewer can skip what does not apply:

1. **Brand compliance** -- direct mapping to Section 3 of the plan.
   Tokens (consume `@paradigm/brand-tokens` v1.0.0+, no hardcoded
   hex, grep-evidence gate), typography, visual language (Lucide
   icons, 4px spacing scale, radius set, shadow set, no
   pointing-at-laptop stock, shift-rule once per major section,
   sentence-case "Paradigm" wordmark).
2. **Accessibility AA** -- WCAG 2.1 thresholds, axe-core in both
   themes, focus ring on every interactive, Iron not Slate for
   light-theme muted text, Onyx on green for primary buttons,
   corrected status-text colors from the Section 3.4 corrections, and
   the manual dual-reviewer pass per Section 5.9.
3. **Lighthouse** -- 90+ performance, 100 accessibility, 100 best
   practices, 100 SEO (marketing only). Mobile profile at 320 and
   768. Zero console errors.
4. **Test coverage** -- three-tier bar (critical 90%+, standard 80%+,
   low-logic visual-regression-only). Floor rule: no coverage drop on
   any file the PR touches. Brand-tokens package has a per-semantic-
   token contrast test requirement.
5. **Security review gate** -- defines the auth surface explicitly
   (portal-auth module, JWT verification, bearer-token issuance and
   hashing, Access policy, new auth-bearing endpoints, CORS behind
   the gate). Triggers two-reviewer split per Section 5.9 (one on
   XSS/CSRF/token storage, one on session/lifetime/logout). All P0
   and P1 fixed before merge. No "this user is Drew" hardcoding.
6. **Deploy verification** -- pre-deploy staging + safe-point tag;
   post-deploy TLS, redirects, reverse-proxy path routing (with the
   explicit S43 deep-link test for the Router-basename failure mode),
   CORS / base-path / JWT-cookie scope, mobile sanity, zero console
   errors, BROOKFIELD_PC acknowledgement; rollback command in the PR
   description.
7. **PR pre-merge checklist** -- a copy-paste block reviewers run at
   merge time. `N/A -- <reason>` is required instead of silent skips.
8. **Intentional non-goals** -- the DoD does not set coding style,
   branching, or release cadence (matches `SESSION_PROTOCOL.md`
   section 10's per-project deferrals).
9. **References** -- pointers to PARADIGM_PLAN.md sections, the
   session protocol, naming conventions, and the brand-tokens
   contract.

### Test coverage numbers I had to set

The plan calls for a "test-coverage bar" but does not give a number.
I set the three-tier framework above. Defensible defaults but Drew
should sanity-check the numbers in PR review. The floor rule (no
regression on touched files) is the load-bearing piece -- the tier
numbers are a starting point.

## What is currently broken or incomplete

Nothing in this deliverable. The DoD is a pure documentation artifact;
no code paths touched, no tests to run.

Two things are deliberately left for Drew's review, not for a future
session to "fix":

- **Placement.** The DoD lives at the root of the `agile-cards` repo
  because this worktree is the agile-cards repo and the instruction
  was to stack a PR. If Drew wants the canonical home to be
  `_meta` (the dev-meta repo, which already houses
  `SESSION_PROTOCOL.md` and `NAMING_CONVENTIONS.md`) or a future
  `paradigm-docs` repo, this is a one-file move + repoint references.
- **Test-coverage numbers.** See note above. Treat as a proposal.

## What the next session should do first

S41 is complete on the artifact axis. The Wave 1 set that can start
next (per Section 9 of the plan): **S01, S16, S20, S26, S27, S33, S43**.
S41 is now also done.

If continuing Paradigm work next, the highest-leverage Wave 1 starts
are:

1. **S01** -- token package scaffold. Gates the entire S02 to S07
   chain, which gates every UI re-skin. Foundation sprint.
2. **S33** -- portal auth implementation. Not on the critical path
   but its S34 security review takes time and the DoD now spells out
   exactly what that review must clear. Starting it early means it
   finishes early.
3. **S43** -- board reverse-proxy base-path migration. Bounded
   config change on agile-cards-board; prerequisite for S36 (portal
   board integration).

If continuing agile-cards runner work instead: chunk 5 picks up where
the 2026-05-20 chunk-4 handoff left off (CLI flag exposure for the
new daemon knobs, see that handoff's "How to run" section).

## Open questions for Drew

- **DoD location.** Stay at `agile-cards/DEFINITION_OF_DONE.md`, move
  to `_meta/DEFINITION_OF_DONE.md` (the dev-meta repo), or stand up a
  `paradigm-docs` repo for it and future cross-cutting Paradigm docs?
- **Test-coverage numbers.** 90 / 80 / visual-only with a "no
  regression" floor -- agree, raise, or lower?
- **Coverage tier mapping for the agile-cards runner specifically.**
  I put claim / merge-gate / eligibility / reaper / amendments under
  "critical." Does that match how you weight them? Anything else in
  the runner you want bumped to critical?

## Pointers

- Canonical plan: `C:\dev\PARADIGM_PLAN.md`, S41 row in Section 5.5.
- New artifact: `DEFINITION_OF_DONE.md` (repo root).
- Session protocol: `C:\dev\SESSION_PROTOCOL.md` (load-bearing
  sections 7 and 9 are quoted in the DoD's deploy section).
- Previous handoff (different workstream, agile-cards runner chunk
  4): `docs/handoffs/HANDOFF_2026-05-20_runner-chunk-4.md`.

## Operational notes

- Branch off `main` at `5a00e74` (the chunk-4 merge commit).
- All git operations ran via PowerShell on the Windows host. This
  worktree is outside `C:\dev\` (under
  `C:\Users\Drama\Desktop\Claude\agile-cards\`), but the rationale
  for `SESSION_PROTOCOL.md` section 7 (Linux git can't reliably
  release `.git\index.lock` left by Windows) still applies to any
  Windows-native checkout, so PowerShell was used for everything that
  touched the index.
- PR opened for Drew's review; do NOT auto-merge.

## How to verify

```powershell
# Confirm the new file is in the tree at the expected location:
Get-Item C:\Users\Drama\Desktop\Claude\agile-cards\lucid-mendeleev-91099f\DEFINITION_OF_DONE.md

# Lint the markdown by reading it; nothing executes.
# No tests, no build steps -- this PR is documentation only.

# Confirm cross-references resolve:
Test-Path C:\dev\PARADIGM_PLAN.md
Test-Path C:\dev\SESSION_PROTOCOL.md
Test-Path C:\dev\NAMING_CONVENTIONS.md
```

End of handoff.

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then the project
`README.md` / `RUNNER_CONTRACT.md` / `SKILL.md`, then this file, then
run `vstart` (with `-Path` since this worktree is outside `C:\dev\`).
For Paradigm-program work specifically, also read
`C:\dev\PARADIGM_PLAN.md` and the new `DEFINITION_OF_DONE.md` at the
repo root.
