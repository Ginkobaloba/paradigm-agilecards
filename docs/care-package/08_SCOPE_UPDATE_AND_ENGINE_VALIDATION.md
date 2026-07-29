# Update: scope reversal + the engine is now validated

**Date:** 2026-07-17
**Status:** THIS IS THE CURRENT AUTHORITY. Where it conflicts with `06_COUNCIL_SYNTHESIS.md` or `07_FABLE_BRIEF.md`, this wins. The earlier docs are preserved unedited because the council record has value; two of their conclusions are now overturned by (a) a Drew ruling and (b) evidence that did not exist when they were written.

Two things changed after the council:

1. **AgileCards is not internal-only.** Drew, 2026-07-17: *"no it is not just an internal tool."*
2. **The engine has now actually been run, and it did not work until today.** The council's central finding ("713 tests, never run") is confirmed and explained: it could not execute a card on Windows at all. It can now.

---

## 1. Scope reversal: internal-FIRST, not internal-only

The council settled `FL-D` on `portal-gameplan-opus` DR-9 (2026-07-08: "internal dev-tool NOT a customer SKU") and, on that basis, cut Postgres, `org_id`, RLS, and multi-tenancy from scope and put Branch A on ice. **Drew has now overridden that.** His exact framing:

> "agilecards is starting as internal only. I think it could be really powerful as an enterprise tool but ... it would need a lot of polish that isn't priority ... it is being made basically as my tool for now but I could see it being an enterprise tool I sell."

So the honest state is **internal-first with a real, deferred SKU ambition** -- not the permanent internal-only ruling the council reasoned from. That changes three conclusions:

- **Multi-tenant Postgres + RLS is back in scope** as the enterprise path. Not "cut"; deferred behind the internal build, but a committed direction rather than a discarded one.
- **Branch A comes off ice.** Its ~4,592 lines of `FORCE ROW LEVEL SECURITY` + two-role + fail-closed work are no longer "for a customer who does not exist." They are for a customer Drew intends to have.
- **The market-incumbent evidence (Linear/GitHub/Jira, Vibe Kanban) is live again**, though still not urgent: it bears on the eventual SKU, not the internal-first build. The deep layer the Researcher identified as empty (worktree, diff, per-card cost, gate verdict on the card) is the eventual differentiator.

**What does NOT change.** Branch A comes off ice as a *reference and starting point*, not as a base to salvage wholesale. Drew's standing instruction holds: build the real backend properly from scratch rather than stitching the rushed duplicate-session WIP. The convergent architecture two sessions independently reached (Postgres 16, SQLAlchemy 2 sync, psycopg 3, Alembic, FORCE RLS, two-role owner/app split, transaction-local `app.current_org`, fail-closed, raw-SQL bypass tests) is validated design input; the *code* is reference, not foundation.

**A design principle Drew attached to the enterprise ambition, worth binding into the build:** maximum customizability now (he does not yet know what works), opinionated minimalism later (enterprise / non-technical users drown in options). Stripping options later is a *reversal*, which his own principle 2 flags as expensive -- UNLESS the variability lives in configuration rather than architecture. **Explore in config, not in schema.** Knobs, tier maps, gate thresholds, board views, YAML: go wild, they are cheap to remove. The card schema and the port contracts: keep narrow. This is exactly why Track L passed the reversal test (variability in adapters + tier-map YAML, narrow core) and Branch A's `org_id`-in-the-schema did not.

---

## 2. The engine is now validated (it was not, until today)

The council's decisive finding was that the engine had 713 passing tests and had never been run against a real backlog; `card_metrics` had zero rows. That is now **confirmed and fully explained, and fixed.** Running it for the first time surfaced four defects, all now fixed and merged/open behind green CI.

### What was broken (all found by running it, none caught by 713 tests)

- **P0: the engine could not prepare a git worktree on Windows.** `_verify_worktree` substring-matched a backslash path (`str(Path)`) against git's forward-slash `worktree list` output -- always False. Every `git worktree add` succeeded, then was rejected by its own post-condition, and the failure left the branch pinned so retries livelocked. **This is why the store was empty. Not neglect -- the engine literally could not execute a card on the platform it runs on.** (So the un-checkable BROOKFIELD store is moot: the same bug would have blocked it there too.)
- **The gate failed OPEN on malformed cards.** A card with zero parseable acceptance criteria verified as `pass`, and the gate issued an `auto` decision and called `gh pr create`. Only GitHub refusing an empty-commit PR stopped a merge. A real executor that committed anything would have merged unverified work. Now fails closed.
- **The shipped template and example produced invalid cards.** They taught `type: command` (and other `lib/verifier` names); the daemon runs `cards_runner/verifier`, whose canonical types are `{file_exists, file_absent, file_contains, file_lacks, shell, subjective}`. Every card authored from the reference material raised `SchemaError`. This is audit **S6** (two-verifier drift) made concrete, and a second reason nothing ever ran.
- **The claim-fail-bounce-retry loop could not retry.** Fixed branch names collided with the prior attempt's retained worktree. Now per-attempt branches.

Why 713 green tests hid a P0 in the claim path: no test in the suite ran `git init`. The real-git path was entirely unexercised; the test file's own docstring promised a fixture-repo test that was never written. The Contrarian's line was exact: "every test is a test of parts against fixtures the authors wrote."

### What is now proven

Fresh rehearsal, throwaway repo, stub invoker (zero tokens), `--pr-gate` on:

- A failing card ran **9 full execute -> verify(fail 3/3) -> bounce -> retry cycles, on 9 distinct per-attempt branches, with 0 worktree collisions.**
- **The gate refused it every time.** No `merge_decision`, no PR, nothing merged.
- The council's pass condition (gate observed blocking a bad card) is **MET**, and the harder bar Drew then set (the whole claim-fail-bounce-retry loop functions, not just one block) is **MET**.
- 718 unit tests pass; the engine battery is green on `windows-latest` CI (the exact platform the P0 was on). New tests shell out to real git.

Shipped as: **PR #57 (CI gates, merged)** and **PR #58 (the four engine fixes, open, all required checks green)**.

### What is still NOT proven

- **The happy path -- a card that PASSES and actually merges -- is untested.** The stub does no work, so it produces no commit, so a passing card still can't open a PR. Proving a green merge needs a real executor (`--invoker sdk-tools`, `ANTHROPIC_API_KEY`, real tokens). That is the **seed run** (the council's Step 2), still outstanding and still Drew's call to spend on.
- **`card_metrics` still has zero rows** because no card has completed a full pass-and-merge. The quoting model still has no observations. Same gate as the seed run.
- **The retry loop is unbounded.** A persistently-failing card churns every poll with no backoff or attempt cap (9 cycles in ~55s). The assessment doc anticipated this churn for weak executors, so it is a product decision (add backoff / max-attempts / route-to-human after N), not a regression. **Flagged for a decision, not fixed.**

---

## 3. Revised sequencing

The council's "run the engine, nothing else" was right, and it has now been done. With a validated engine, the plan advances:

1. **DONE:** CI gates fixed (#57). Engine execution fixed + validated (#58). Rehearsal proves the failure loop.
2. **NEXT, Drew-gated on token spend:** the seed run. Real executor, 5-10 tier-1/2 cards on a low-stakes repo, `--pr-gate` on, Drew reviews all. This proves the happy-path merge, seeds `card_metrics`, and produces the first `human_review_wall_seconds` data -- the only measurement of the review-bandwidth risk the council could not price.
3. **THEN, the enterprise backend** (the scope reversal): Postgres + RLS + tenancy, built properly from scratch with Branch A + the convergent ADR as reference. This is now a legitimate build *because the engine underneath it works* -- building it earlier would have been a multi-tenant backend on an engine that could not execute a card.
4. **In parallel / unchanged:** the audit's must-fix items that survive (deploy artifact for the current stack, the marketing/Gantry cleanup is now moot since there is no external site yet), and the K/L/S/P track (KL3+, Track S, Track P) under its own owner.

The retry-churn decision (§2) and the seed-run go/no-go are the two open gates.

---

## 4. What each earlier doc now gets wrong (so nobody re-inherits it)

- **`06_COUNCIL_SYNTHESIS.md`** -- its "internal dev-tool" premise and its "cut Postgres/RLS/tenancy, ice Branch A" recommendation are overturned by §1. Its "run the engine" recommendation is executed (§2). Its process findings, disagreement map, and the raw transcript (`05`) stand.
- **`07_FABLE_BRIEF.md`** -- "do not build a schema/dashboard/control plane, run the engine" is substantially done. Its "out of scope: Postgres/org_id/RLS/tenancy" list is reversed by §1. Its token-delegation discipline and hard constraints still apply to whatever gets built next.
- **`01`/`02`** -- already carried supersession notices; unchanged.
- **`03` (research), `04` (prompt), `05` (transcript)** -- historical, still valid, untouched.

---

## 5. Honest ledger of my own errors this session (truth over comfort)

The person reading this should know where I was wrong, not just where the engine was:

- I sourced the council's two original fault lines (FL1, FL2) from a stale status doc while my own audit method said not to. The council caught both.
- My first rehearsal card was mangled by PowerShell backtick-escaping, which I briefly read as a catastrophic verifier bug before checking myself.
- I wrote 179 em-dashes across the package before catching a hard style rule.
- Twice, a sandbox guard blocked a `Remove-Item` in my cleanup and I had to reroute.

None of these changed the substance, but the pattern is worth naming: the real findings in this session came from *running the thing*, not from reading about it -- which is the same lesson the empty store taught.
