# Handoff: /cards extracted into agile-cards repo

**Date:** 2026-05-17
**Session:** Cowork, repo extraction
**Branches involved:**
- `dev-meta` main (received the merged v1.1 PR)
- `dev-meta` `chore/cards-migrated-to-agile_cards` (open PR #2)
- `agile-cards` main (new repo, published at https://github.com/Ginkobaloba/agile-cards)

**Predecessor handoffs:**
- `HANDOFF_2026-05-16_cards-skill-v1.md` (in this folder)
- `HANDOFF_2026-05-17_cards-skill-v1.1.md` (in this folder)

---

## What this session did

The /cards skill outgrew being a folder inside `_meta`. It got its own
local repo at `C:\dev\agile_cards`. The git history of every commit Drew
made to the cards work was preserved end-to-end via `git subtree split`,
so the new repo's log shows the original authorship and timestamps. No
content was lost. The old location in `_meta` is left as a tombstone
until the new repo is published on GitHub.

### Order of operations

1. **Merged dev-meta PR #1.** The v1.1 consultant-fixes branch was the
   latest cards state and we wanted the new repo to start from that.
   `gh pr merge 1 --merge` produced merge commit `76ea284` on
   `dev-meta` main. All checks were green; merge was a clean
   fast-forward-able merge commit.
2. **Subtree-split cards history out of `_meta`.** From `_meta` on the
   updated main, `git subtree split --prefix=skills/cards -b cards-only`
   produced a branch with two commits containing only the `skills/cards/`
   subtree, rewritten as if those files had always been at the repo
   root. Original author (Drew Mattick) and original timestamps
   (2026-05-16 and 2026-05-17) preserved.
3. **Initialized `C:\dev\agile_cards`** with `git init -b main` and
   pulled the `cards-only` branch from `_meta` to seed the history.
4. **Fixed two cross-references to `_meta` inside the cards docs.**
   `tests/atomic_rename_test.ps1` and `tests/synthetic_dry_run_walkthrough.md`
   each carried `cd C:\dev\_meta\skills\cards` example paths. Both
   were retargeted to `C:\dev\agile_cards`. Committed as
   `post-migration: retarget test example paths from _meta to agile_cards`.
5. **Scaffolded the new repo.** Added `.gitignore` (OS, editor, sync,
   test-output, and runtime-data exclusions), a `LICENSE` placeholder
   noting the MIT-vs-Apache-2.0 decision is open, and a rewritten
   `README.md` that reads as an independent-project README rather than
   a section of a meta-repo spec. Dropped the stray `.synctest.delete-me`
   artifact. Moved `PHASE1_OVERVIEW.md` and `SKILL.md.outline.md` into
   `docs/history/` then later deleted them because their content was
   already deprecated stubs ("safe to delete") with no real text.
6. **Imported the two cards-related handoffs from `_meta/docs/handoffs/`.**
   Used `git format-patch -- <file paths>` from `_meta` against the
   relevant commits, then `git am --3way --empty=keep` in agile_cards.
   This preserves original author and date in the new repo, contained
   to just the handoff changes (the SKILL.md / RUNNER_CONTRACT.md edits
   from those same commits already came across via the subtree split,
   so no content is duplicated).
7. **Cleaned up `_meta` on a new branch.** Branch
   `chore/cards-migrated-to-agile_cards` replaces
   `skills/cards/README.md` with a MOVED notice (leaving the rest of
   the cards folder in place as a stale snapshot), deletes the two
   cards handoffs from `docs/handoffs/`, and adds a clarifying note to
   `install.ps1`'s docstring. Pushed and opened as
   `https://github.com/Ginkobaloba/dev-meta/pull/2`.

### Why subtree split, not filter-repo or just-copy

`git subtree split` was the right tool because cards lived under a
single prefix (`skills/cards/`) in `_meta` and we wanted the new repo
rooted at that prefix with full history. It runs in seconds on a small
prefix and produces a clean branch that any new repo can pull from.
`git filter-repo` would have worked too but is overkill for a
two-commit slice. A pure copy (the fallback Drew authorized) was not
needed; the split worked on the first try.

The handoffs were trickier because they live in `_meta/docs/handoffs/`
alongside non-cards handoffs (the original bootstrap handoff is in the
same directory), so a subtree split on `docs/handoffs/` would have
pulled in unrelated work. `git format-patch` with explicit path
restriction was the cleaner cut.

---

## State after this session

### agile-cards (`C:\dev\agile-cards`, pushed to GitHub)

Layout:

```
.gitignore
LICENSE                 (TBD - MIT vs Apache-2.0 open)
README.md
SKILL.md
RUNNER_CONTRACT.md
tier_map_claude.yaml
tier_pricing.yaml
templates/
    card.md
    batch_manifest.yaml
    project_config.yaml
examples/
    b001-03-add-rate-limit-middleware.md
tests/
    atomic_rename_test.ps1
    synthetic_dry_run_walkthrough.md
docs/
    handoffs/
        HANDOFF_2026-05-16_cards-skill-v1.md
        HANDOFF_2026-05-17_cards-skill-v1.1.md
        HANDOFF_2026-05-17_repo-migration.md  (this file)
```

Git log (oldest first):

```
9eaf2c4  2026-05-16  feat(cards): ship /cards skill v1 ...
cb4fea1  2026-05-17  cards: v1.1 consultant fixes ...
ff8b686  2026-05-17  post-migration: retarget test example paths ...
c11409d  2026-05-17  scaffold agile_cards as independent repo
4d52c9f  2026-05-16  feat(cards): ship /cards skill v1 ...   (handoff slice)
a441654  2026-05-17  cards: v1.1 consultant fixes ...        (handoff slice)
c266056  2026-05-17  cards: backfill v1.1 handoff ...        (handoff slice)
640da01  2026-05-17  drop deprecated phase 1 stub docs
```

The "handoff slice" commits are the `git am` results from the
`format-patch` restricted to handoff paths. They carry the same
commit messages and author dates as the original commits in `_meta`,
but the diffs only contain the handoff files (the SKILL.md /
RUNNER_CONTRACT.md changes from those same commits already came in via
the earlier subtree-split commits). This is the cost of the path-
restricted import: the log shows the commit "twice" for the same
session of work, once for the code and once for the docs. Acceptable
trade-off for keeping authorship intact.

### dev-meta

- `main` includes the merged v1.1 PR (commit `76ea284`).
- Branch `chore/cards-migrated-to-agile_cards` is pushed, PR #2 is
  open: replaces `skills/cards/README.md` with a MOVED notice, deletes
  the two cards handoffs, adds a docstring note to `install.ps1`.
- The old `skills/cards/` folder still contains stale copies of
  `SKILL.md`, `RUNNER_CONTRACT.md`, etc. They are stale by design
  until the new repo is published and the whole folder gets deleted
  in a follow-up.
- Local working branches `cards-only` and
  `feature/cards-v1.1-consultant-fixes` were deleted. The
  `origin/feature/cards-v1.1-consultant-fixes` remote branch was left
  intact (gh pr merge ran with `--delete-branch=false`).

### Runtime data (`C:\dev\todo\`)

Untouched. Still a separate folder, still not under git. The skill
references it but does not contain it.

---

## Decisions locked in this session

### License: PolyForm Noncommercial 1.0.0

Decided 2026-05-17. Reasoning: best fit for "show the code, monetize
later." Anyone can read, study, fork, modify, and use for
noncommercial purposes (personal, research, education, nonprofit,
government). Commercial use requires a separate arrangement, which
keeps the path open to license this differently for a product or
contract later without retroactively granting commercial rights to
public clones.

Implemented in commit `e3d03d0`
("chore: add PolyForm Noncommercial 1.0.0 license"). The full license
text from the official PolyForm site lives at `LICENSE`, with a
`Required Notice: Copyright 2026 Drew Mattick` line near the top.
README references it. MIT and Apache-2.0 were the other candidates
and were rejected because both permit commercial use.

### Repo name: kebab-case

Decided 2026-05-17. The local folder was renamed from
`C:\dev\agile_cards` to `C:\dev\agile-cards` to match the rest of the
Ginkobaloba kebab-case convention (`dev-meta`, `project-vector`,
`career-search`).

### Visibility: public

Decided 2026-05-17. Portfolio value to recruiters and other developers
outweighs the privacy benefit. Commercial protection comes from the
PolyForm Noncommercial license, not from access control.

### Fate of `_meta/skills/cards/`: tombstone now, delete later

Decided 2026-05-17. PR #2 on dev-meta keeps the existing folder in
place with a MOVED notice on its README. The full folder gets deleted
in a follow-up PR once Drew confirms `agile-cards` is the canonical
source and nothing depends on the stale copy. Submodule conversion
considered and rejected for now as more friction than value.

## Live state after this session

### Remote: `https://github.com/Ginkobaloba/agile-cards`

Public, default branch `main`. Description: "A tiered card system for
solo-with-agents agile delivery. Dual-axis model routing,
parallel-claimable cards, machine-checkable acceptance criteria."

Local HEAD `e3d03d0` matches `origin/main`. Push verified via
`git ls-remote origin main`.

Created via:

```powershell
cd C:\dev\agile-cards
gh repo create Ginkobaloba/agile-cards --public --source=. --remote=origin --description "A tiered card system for solo-with-agents agile delivery. Dual-axis model routing, parallel-claimable cards, machine-checkable acceptance criteria."
git push -u origin main
```

## What's still required from Drew

### Action: review and merge PR #2 on dev-meta

`https://github.com/Ginkobaloba/dev-meta/pull/2` is open. The tombstone
README in `_meta/skills/cards/` was updated in this session to point at
the live `https://github.com/Ginkobaloba/agile-cards` URL (no more
`<TBD>` placeholder). Review and merge when ready. Until that PR
lands, dev-meta `main` still ships the unmodified old copy of the
cards skill, which would confuse anyone walking in cold.

### Follow-up: delete `_meta/skills/cards/` entirely

After PR #2 is merged and `agile-cards` is proven to be the live
source, open a follow-up PR on dev-meta that deletes the whole
`skills/cards/` folder. Suggested commit message:
`chore: remove stale skills/cards tombstone (lives at agile-cards now)`.

---

## Open questions for future sessions

1. Should the sprint scheduler / dashboard, when it gets built, live
   as a submodule of `agile-cards` or as its own peer repo? The README
   currently promises a submodule. Submodule pros: one clone gets you
   the whole system. Cons: submodule UX is famously rough. Worth
   revisiting when the scheduler actually starts shipping.
2. Is there a value in a `CHANGELOG.md` separate from the handoffs? The
   handoffs are detailed but session-shaped. A trimmed semver-aligned
   CHANGELOG would be friendlier to anyone landing on the repo cold.
   Not blocking.
3. Should `tier_pricing.yaml` move out of the planner repo and into
   the runtime data folder? Argument for: it's mutable Anthropic-side
   data, not a planner constant. Argument against: cards record token
   counts; the price table is what the planner uses to compute
   `cost_cap_usd`, so it has to be reachable at planning time.
   Currently it lives with the planner; flagged for review if a
   second provider's pricing ever gets added.

---

## Sanity check results

- `git status` clean on `agile-cards` main.
- All expected artifacts present (SKILL.md, RUNNER_CONTRACT.md,
  templates/, examples/, tests/, docs/handoffs/).
- `tests/atomic_rename_test.ps1` parses cleanly via the
  PowerShell AST parser.
- No stray references to `_meta\skills\cards` outside the two
  pre-existing handoffs (where they are historically accurate and
  should stay).
- `dev-meta` PR #2 created and visible at
  `https://github.com/Ginkobaloba/dev-meta/pull/2`.
