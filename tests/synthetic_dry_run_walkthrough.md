# Synthetic dry-run walkthrough

This is the test invocation requested in the phase 2 brief, in the form
of a documented walkthrough rather than a live shell run. The purpose is
to verify that the dry-run summary path produces an output Drew can
approve or reject before any cards are written.

The walkthrough uses a deliberately tiny story so the planner has
something to plan but the result stays inspectable.

---

## Synthetic story

```
As a developer running a fleet of agents, I want a small CLI helper
that reports which cards in C:\dev\todo\backlog\ are immediately
claimable (no unmet dependencies) so I can sanity-check the runner's
claim order without trusting it blindly.
```

Project: `C:\dev\agile_cards\` (we'll eat our own dog food)
Mode: full (no .cards-config.yaml exists -> defaults)

---

## Expected planning pass output (planner + reviewer, both opus)

**Planner proposal (round 1):**

| id | title | stakes | difficulty | tier | depends_on | touches |
|---|---|---|---|---|---|---|
| b001-01-define-claim-readiness | Define "immediately claimable" formally | low | shallow | 1 | [] | docs/claim-readiness.md |
| b001-02-parse-card-frontmatter | Parse card frontmatter from a markdown file | low | shallow | 1 | [] | tools/parse_card.py |
| b001-03-walk-backlog-dir | Walk backlog/ and yield card paths | low | shallow | 1 | [] | tools/walk_backlog.py |
| b001-04-resolve-dependencies | Check each card's depends_on against done/ | medium | shallow | 3 | [b001-02, b001-03] | tools/resolve_deps.py |
| b001-05-render-cli-output | Render claimable list with id + title + tier | low | shallow | 1 | [b001-01, b001-04] | tools/cards_ready_cli.py |
| b001-06-add-pytest-coverage | Tests for parse_card, walk_backlog, resolve_deps | medium | shallow | 3 | [b001-02, b001-03, b001-04] | tests/test_cards_ready.py |

**Reviewer critique (round 1):**

- b001-01 is undersized to be its own card. Roll into b001-05 as a one-paragraph header comment.
- b001-04 and b001-06 share no touches, can run in parallel after their deps are done. Good.
- No parallel hazards detected.
- Suggest tier-bumping b001-04 to 4 (deep) because "card is done iff merge_status=merged" is a non-obvious invariant the executor might miss. Planner pushed back: the spec is already explicit in README.md; tier 3 is fine.

**Disagreement logged for b001-04 tier (planner 3 vs reviewer 4).**

**Planner revision (round 2):**

- Drop b001-01. Final batch size: 5 cards.

---

## Expected dry-run summary (rendered to Drew)

```
/cards proposed batch b001 for C:\dev\agile_cards (mode: full)

5 cards.
Points histogram:
  tier 1:  3
  tier 3:  2

Dependency edges (3):
  b001-02-parse-card-frontmatter -> b001-04-resolve-dependencies
  b001-03-walk-backlog-dir       -> b001-04-resolve-dependencies
  b001-04-resolve-dependencies   -> b001-05-render-cli-output
  b001-02-parse-card-frontmatter -> b001-06-add-pytest-coverage
  b001-03-walk-backlog-dir       -> b001-06-add-pytest-coverage
  b001-04-resolve-dependencies   -> b001-06-add-pytest-coverage

Immediately claimable: 2  (b001-02, b001-03)
Parallel hazards: 0
Planner-reviewer disagreements: 1
  - b001-04 tier: planner=3, reviewer=4. Defaulted to planner.

Validation:
  DAG acyclic: yes
  Card count >= 5: yes
  Parallelism present: yes (3 cards have no unmet deps after b001-04)
  Hot paths touched: 0

Type 'ok' to write cards. Type any edits or revisions otherwise.
Type 'abort' to discard.
```

This is the dry-run summary the skill must produce. The shape is what
matters for the v1 contract; the exact tier reads for these synthetic
cards are illustrative.

---

## What this walkthrough verifies

- The planner-reviewer disagreement logging path exists and is shown
  inline before approval.
- The DAG cycle check runs against the proposed graph before approval.
- Card count and parallelism heuristic are surfaced.
- Drew can read the summary in under 30 seconds and approve or revise.
- No card files exist on disk yet at this stage.

---

## What this walkthrough does NOT verify

- Actual planner-agent output quality (depends on opus + the prompt
  template, which is owned by SKILL.md and the agent spawn site).
- Atomic file-move behavior on NTFS (see `atomic_rename_test.ps1`,
  named for the underlying syscall; verifies that moving a card file
  between subfolders is atomic under concurrent contention). Run
  separately on each device.
- Runner claim/merge/done transitions (out of scope; runner contract
  in `RUNNER_CONTRACT.md`).

---

## Mount-cache desync caveat

This walkthrough was authored in a session where the Linux mount used
by the test sandbox cached a stale view of files written by the Edit
tool on the Windows side, making live python parse-validation
unreliable. The Read tool consistently saw the canonical Windows file
contents. Subsequent sessions running on Windows directly (with `vstart`
and PowerShell) should not hit this issue, and re-running a python
validator against the same files on the actual host is the recommended
follow-up.
