# agile-cards-board Rules

Operational rules for this repo. These are project-local additions to `C:\dev\SESSION_PROTOCOL.md`; the protocol still wins on anything it covers.

## 1. v3 means halt

> Opening any branch with a `-v3` (or higher) suffix requires writing a halt-paragraph first explaining why the prior versions cannot ship as-is. Branches without this paragraph are subject to deletion at the next sweep.

The halt-paragraph lives at `docs/handoffs/HANDOFF_<feature-slug>_<reason>_<YYYY-MM-DD>.md`. It must cover, at minimum:

1. What the feature was supposed to do.
2. What v1 tried and where it stopped.
3. What v2 tried and where it stopped.
4. What v3 would try, and the concrete reason v2 cannot ship as-is.
5. Which version is canonical.
6. What would need to happen to ship the canonical version.
7. An explicit "do NOT open a v4 without first writing a paragraph explaining why the canonical version cannot be shipped as-is" line.

### Why this rule exists

The 2026-06-18 third-party retro on `C:\dev\` found four features on this repo (`card-event-timeline`, `cmdk-filter-views`, `manual-rank`, `tile-polish`) that existed as v1+v2 or v1+v2+v3 rewrites, all stalled the same four weeks ago, none merged. The actual cause was a stacked-PR auto-retarget trap that lied to GitHub about merge completeness; the subsequent v2 and v3 branches were recovery cherry-picks of identical implementations onto progressively newer bases. None of the v2/v3 branches represented a redesign; they represented "the last attempt did not land, let's rebase and try again." That is the pattern this rule is written to stop.

The cost is not the disk space. The cost is the cognitive load of "wait, which one is canonical" the next time anyone, human or agent, touches this corner of the board. Multiply that across four features and the answer to "what is shippable right now" becomes "ask Drew," which means throughput is gated on Drew's attention, which means nothing ships.

### What counts as "v3"

Any branch suffix matching `-v[0-9]+` where the integer is 3 or higher. So `-v3`, `-v4`, `-v3a` (if anyone gets cute), all count. A branch named `feature/foo-rewrite` or `feature/foo-take-3` is exempt from the literal rule but is in spirit a v3, and a halt-paragraph is still a good idea before you start.

### What the next sweep will do

Periodic cleanup sweeps (next one targeted Sunday-night via `_scripts\sweep.ps1` once that lands, per retro move 5) will:

1. List every branch with a `-v3` or higher suffix.
2. Cross-reference each one against `docs/handoffs/` for a halt-paragraph.
3. Surface any v3+ branch without a paragraph to Drew as a deletion candidate.

Deletion is not automatic. The sweep proposes; Drew approves.

## 2. No git push from a Linux/WSL sandbox into a Windows-side repo

This is `C:\dev\SESSION_PROTOCOL.md` §7 applied at the repo level. If the sandbox needs a git operation that writes to origin or rewrites the index against a Windows-mounted working tree, it stages a PowerShell script under `scripts/` for Drew to run from a clean PowerShell session. The retro flagged this as the recurring cause of corrupt indexes and phantom branches across half the frontend repos in `C:\dev\`. We do not get to violate it because the work is small.

## 3. Handoff docs ship with the work

Every session that touches this repo writes a `docs/handoffs/HANDOFF_<YYYY-MM-DD>_<topic>.md` entry, even if the topic is "I cleaned up branches." The retro called this discipline out as one of the things this codebase is doing right; the rule is to keep doing it.

## 4. Stacked PRs require a written merge order

If a session opens more than one PR in a stack, the head PR's description (or a handoff doc) must spell out the merge order explicitly. The card-event-timeline stranding on 2026-05-20 happened because GitHub flipped four PRs to MERGED without anyone noticing the upstream commits never reached main. A documented merge order is the cheapest possible defense against that.
