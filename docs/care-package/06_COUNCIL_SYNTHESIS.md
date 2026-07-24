# Council Synthesis -- AgileCards board-as-control-plane

> **PARTIALLY SUPERSEDED (2026-07-17) by `08_SCOPE_UPDATE_AND_ENGINE_VALIDATION.md`.** Two of this document's conclusions no longer hold: the "internal dev-tool, cut Postgres/RLS/tenancy, ice Branch A" recommendation is reversed (Drew: AgileCards is not internal-only), and the "run the engine" recommendation has been executed -- the engine was found unable to run a card on Windows and is now fixed and validated (PRs #57, #58). The process findings, the disagreement map, and the raw transcript (`05`) all stand.

**Companion to:** `05_COUNCIL_TRANSCRIPT_FULL.md` (raw, verbatim). This is the decision document.
**Mode:** `/council deep` (3 rounds + 2 orchestrator verification passes)
**Date:** 2026-07-16
**Synthesized:** 2026-07-17

---

## 1. Topic restatement

Should AgileCards commit to the "board-as-control-plane for the agent engine" pivot, and given the parallel-session reconciliation, what is the right sequencing and final scope? The council was handed two fault lines to settle (which store owns a card; whether the Postgres `0001` migration must be re-cut), Branch A's fate, and a three-phases-out test to apply to every phase of both plans.

**What actually got deliberated:** something narrower and more useful. Both handed-down fault lines turned out to be false. What the council converged on instead is that the engine has never been run and the system has never been observed.

---

## 2. Bottom lines (one line per persona, final position)

- **Contrarian:** The market pivot is dead (conceded to the Researcher's primaries), the *tool* survives; cut Postgres/`org_id`/RLS/tenancy from Phase 1 entirely, run the engine's SQLite, and prove `--pr-gate` on a throwaway repo before pointing it at anything Drew cares about. **Confidence: high** (killed both its own R1 lead and its own R2 proposal on evidence).
- **Expansionist:** The only surviving expansion is the measurement/cost layer, and it is *not* cheap surfacing work as claimed; it is "run the engine to get rows, then surface them." Do the run, not the dashboard. **Confidence: high on the run, withdrawn on the pricing.**
- **Researcher:** The shallow layer of the category is occupied and defended (Linear for Agents 2025-05-20, GitHub agent activity 2026-03-26, Jira Rovo GA May 2026 -- all ship agents-as-actors); the **deep layer is empty** (nobody puts a worktree, a diff, a per-card spend, or a machine-checked gate verdict on a card). Per-trace cost attribution is commodity (Langfuse, Cursor admin analytics, Anthropic Enterprise Analytics API, March 2026); only the cost-to-rework-to-contract-survival join at card grain is distinctive. **Confidence: high, with self-corrections on both R1 headlines.**
- **Logician:** The pivot argument as written was invalid *and* unsound; the internal-tool version is valid and does not use P2 at all, which means the market framing was decoration on a decision Drew already made. One root question remains (tool or SKU) and it is already answered. **Confidence: high.**
- **User-proxy:** FL-D is settled by a Drew ruling, not a guess (DR-9, 2026-07-08: "Agile Cards STAYS ... internal dev-tool NOT a customer SKU", `listedInCatalog=false`), corroborated by `[[project_agilecards_agentic_vision]]` 2026-07-16 calling the pivot "a dogfooding argument, not a hypothetical market claim." Cap the first run at 5-10 cards Drew personally reviews. **Confidence: high (upgraded from medium after re-reading memory).**

---

## 3. What the debate changed

This is the main event. Nearly everything the council was asked ceased to exist during the deliberation.

**The orchestrator's own two headline fault lines were destroyed, and the orchestrator verified them false with its own checks.** State this plainly, because `01_RECONCILIATION_MEMO.md` and `02_REVISED_HANDOFF.md` are wrong on their two highest-stakes claims and anything downstream of them is contaminated:

- **FL1 ("card files on disk are the source of truth") is false.** The engine has been database-canonical since chunk 2b. `daemon.py` header: "The store is the single source of truth ... The claim is now a transactional conditional `UPDATE` in the card store." `_try_claim` calls `self.repo.claim_card(...)`, not a file move. `store/README.md`: Model B, database-canonical, card file preserved as a per-run projection. The error's origin is traceable: the memo trusted `AGILECARDS_MVP_LOCALGPU_ASSESSMENT_2026-07-14.md`, a status doc that is stale against `main`, which violates the audit's own stated method of treating status docs as untrusted. **Options (a)/(b)/(c)/(d) are moot as framed.** Drew settled this in May.
- **FL2 ("`0001` carries zero agent-native model") is false of the product.** `store/schema.py` `CARD_COLUMNS` already carries `claimed_by`, `attempt_trace_id`, `model_used`, `merge_status`, `verified_at`, `verified_by`, `estimated_tokens`, `actual_tokens`, `trace_id`, `pr_url`, `work_type`, `stakes`, `difficulty`, with `tenant_id` as first PK column. The model is absent from the *backend's* `0001`, not from the repo. Branch A did not fail to invent it; it failed to look thirty files away.

**Concessions, in order of how much they cost the conceder:**

- **Contrarian conceded twice, both times fatally to its own position.** R1 it endorsed "nobody is modeling an agent as a first-class actor" by assertion, having just broken FL1 by grepping. The Logician caught the double standard ("your evidentiary standard was applied to one premise and not the other"), the Researcher's dated primaries killed it, and the Contrarian withdrew "the pivot survives" as stated. Then in R3 it killed **its own FL-C proposal** on evidence it went and found: the board's `0001` creates 10 tables, the engine's `schema.py` creates 8, the overlap is **exactly 2** (`cards`, `card_events`). "Put Phase 1 behind `CardRepository`" therefore means either bolting sprints and retros onto the daemon's port, or running two stores plus a seam. Its own words: "FL-C is that outcome wearing better clothes, and I helped dress it."
- **Expansionist withdrew its own headline pricing.** R1: the measurement layer is "surfacing work, not building work," near-zero marginal cost. R3, after verification #4: "I priced a wired writer as if it were data. It is plumbing over an empty table." Also withdrew category ownership entirely and parked platform extraction dead.
- **Researcher self-corrected in both directions, and this is the most valuable single move in the transcript.** Too strong: "the exact product already shipped and died" does not survive -- **Bloop died, Vibe Kanban did not.** It went Apache-2.0 community-maintained and the niche refilled within weeks (Nimbalyst, Parallel Code, forks). A niche that refills immediately is a demand signal, not a graveyard. Too weak: it had let "agents are assignable" stand for "the category is occupied," which is lazy and checkable. GitHub's agent surface renders session status only (queued/working/waiting for review/completed) and explicitly no branch, worktree, diff, token cost, or gate verdict. Linear deliberately makes agents `delegate` not `assignee` -- a humans-keep-ownership design, not an execution-control design. **FL-A resolves in a place neither original position occupied.**
- **Logician conceded to the orchestrator's verification against itself:** "I certified premises I had not checked, which is the same sin I charged the memo with." Also killed its own split-by-field option: it presupposed the file tree owns content; the store owns both, and the field split it proposed already exists as columns inside one schema.
- **User-proxy conceded its FL1 (b) vote** (extrapolated from worktree-per-agent, flagged as extrapolation, wrong -- `projection.py` already delivers the worktree ergonomics it was protecting), then conceded the thing its whole position rested on: "I said 'populated.' It isn't. That's on me, and it's the worse kind of error." Its own diagnosis is worth keeping: `[[nexus-outperform-program]]` is the same shape (0/50 real-oracle, fabric never run live). **"I have a pattern of building the engine and admiring it."**

**Objections answered:**

- "Re-cutting `0001` violates principle 2" -- answered by both the User-proxy and the Logician, who then noted they were arguing past each other. `0001` is unshipped, unmerged, holds zero data, has zero dependents. Principle 2 forbids *deferring known flaws*; it says nothing about sunk keystrokes. Re-cutting is principle 2, not a violation of it. That was never the real objection, though: the real one was "a re-cut produces a fourth schema against an existing seam," and that objection died with FL-C.
- "The gate is a no-op" -- narrowed honestly by its own author. `--pr-gate` exists (`cli/__main__.py:82`), and `:126` says production runs typically set it. What survives: it defaults off in three places (`types.py:218`, `doctor.py:138`, the CLI), it degrades silently rather than refusing to run, and nothing measures whether it has ever run enabled. **It is a knob, not a no-op.**
- "Drew's binding test" -- the Logician repaired it and the council adopted the repair. "Still standing unmodified three phases out" proves too much: it forbids all incremental development and would fail every phase including the ones the memo passes. The honest version: **will future work EXTEND this decision or REVERSE it?** Applied: `PostgresRepository` behind `CardRepository` passes; a parallel `0001` keyed on `org_id` fails; Track L passes.

**The single most consequential finding arrived in verification #4, after Round 2:** the default store spec is `sqlite:<todo-root>/cards.db`, todo-root defaults to `C:\dev\todo`, and **`C:\dev\todo\cards.db` does not exist.** Zero cards in `backlog/`, `active/`, `done/`, `blocked/`, `amendments/`; one file in `_batches/` (the counter); last touched 2026-05-16/17. The `LedgerWriter` is correctly wired (7 call sites in `daemon.py`), so the ledger *would* populate. It never has. **713 passing tests and zero rows.** The Expansionist independently reproduced it in R3.

**One line for the whole thing:** every persona moved, two withdrew their own headline claims on their own evidence, the orchestrator's two headline fault lines were both destroyed, and the market framing that motivated the entire package was disclaimed by Drew's own vision doc eight days before the council convened.

---

## 4. Disagreement map (final ledger, in prose)

**Resolved, with the resolution:**

- **FL-A (is the agent-as-actor category empty?)** -- Contrarian said yes by assertion, Researcher said no with dated primaries, and the answer is **neither**. The shallow layer is occupied and defended; the deep layer (worktree, diff, per-card spend, machine-checked gate verdict on the card) is empty. Strongest surviving argument on each side: incumbents genuinely ship agents-as-actors (primary sources, dated); nobody ships execution control (GitHub's own changelog enumerates what it renders, and it is session status only).
- **FL-B (which store owns a card?)** -- **Withdrawn.** Not a decision, a fact, settled by Drew in May. Verified twice against `origin/main` @ `7c9ce99`.
- **FL-C (Phase 1 = `PostgresRepository` behind `CardRepository`?)** -- **Conceded by both proponents.** The 2-of-10 table overlap kills it, and the engine has **zero RLS anywhere** (`git grep -rniE "row level|force row|current_setting|create policy|enable row" origin/main -- engine/runner/src` returns zero hits; the default store is SQLite, which cannot have RLS). `store/README.md`'s "the `tenant_id` column already in the schema is what makes this a policy addition rather than a migration" is a **promise about unbuilt code**. Branch A actually built `ENABLE` + `FORCE ROW LEVEL SECURITY`, `current_setting('app.current_org')` policies, an `audit_events` table, and fail-loud tests. Trading built-and-tested isolation for a documented one, to rename `org_id` to `tenant_id`, is a security regression with aesthetics attached. Survives only as a future note: *if* the board ever needs engine-authored `cards`/`card_events`, read them through the port, read-only, no writes, no PM tables.
- **FL-D (internal tool or SKU?)** -- **Resolved by evidence, not argument.** DR-9, a Drew ruling dated 2026-07-08. Corroborated 2026-07-16. The market frame was never live. This silently decides FL-C, and the Logician's diagnostic is the sharp version: the internal-tool argument is valid and **does not use P2**. A premise whose refutation does not touch the conclusion was never load-bearing. "Which means the market framing was decoration on a decision Drew already made. Not a repair. A different argument that happens to point the same way, which is exactly how motivated reasoning looks from the inside."
- **FL-E (the binding test)** -- **Adopted, uncontested.** Reversal, not modification.
- **FL-F (were the pivot and the architecture call wrongly bundled?)** -- **Resolved by collapse.** There is one root and it was already answered.
- **FL-G (is the ledger the surviving differentiator?)** -- **Resolved, heavily narrowed.** Cost attribution is commodity; the distinctive join needs rows that do not exist. Author withdrew the pricing.
- **FL-H (Vibe Kanban's death)** -- **Withdrawn by its author.** Wrong object, and irrelevant under FL-D anyway.

**Where the council did not converge -- two places, and only two:**

1. **FL-I's target.** Total convergence on *what to do* (run the engine with `--pr-gate` on, generate the first real rows), with a live nuance about *where*. Contrarian: throwaway repo first, because otherwise the first live run is simultaneously the first test of the safety control **and** the first thing the safety control is protecting, which is backwards ordering. User-proxy: throwaway **or** low-stakes real repo, explicitly not `paradigm-agilecards` (don't let it eat itself). These are compatible; the recommendation below adopts both as a two-step.
2. **The unpriced risk nobody resolved.** A working engine produces agent PRs Drew must read. **Success here makes his binding constraint worse.** Raised by the Contrarian in R3, seconded by the User-proxy, priced by nobody.

---

## 5. Synthesis

The council converged, hard, and not on the question it was asked.

**Converged:**
- The pivot as a *market* bet is dead and was never live. As a *tool* bet it needs no defense: we own the engine, we operate it, review is the binding constraint, the board makes review cheap, therefore build the board. Competitors are irrelevant to a tool you build for yourself.
- No store decision is required. The store decision was made in May and shipped.
- Phase 1 has no Postgres, no `org_id`, no RLS, no tenancy. There are no tenants and a ruling says there will be none. Run the engine's SQLite. Ice Branch A intact for the day a tenant exists; its RLS mechanics are real and correct, they are just for a customer who does not exist.
- Nothing gets designed, dashboarded, or unified until the engine has been run once and produced rows.

**Contested and honestly unresolved:**
- Whether the first run should be gated behind a throwaway rehearsal (Contrarian: yes, on ordering grounds) or can go straight to a low-stakes real repo (User-proxy: acceptable). Cheap enough to just do both.
- What happens to Drew's review bandwidth when the engine works. Nobody has a number. The User-proxy's flip condition is the closest thing to a test and it admits it is guessing the threshold.
- Whether the "days of churn, not months" quoting story for Paradigm Coding Solutions survives contact with data. Right now it is, in the User-proxy's words, "vibes with a schema attached."

---

## 6. Recommended path

**Run the engine. Nothing else, until it has run.**

Concretely, and in this order:

**Step 0 (today, costs an afternoon).** Fix the CI gates. `continue-on-error: true` on the live board's 96 tests plus the non-required FastAPI auth suite is why "1,100 green" is a number rather than a guarantee. The audit called it the highest-ROI item in the document and it is *still open* while the council debated schema dialects. Also commit Branch B's ~15 uncommitted files to a throwaway branch; they are one `git clean` from gone.

**Step 1 (the rehearsal, hours).** Point the engine at a **throwaway repo with fabricated cards**, `--pr-gate` on. The acceptance condition is the Contrarian's, verbatim: gate observed **blocking a bad card**, ledger non-empty. This is deliberately a test of the safety control in a place where nothing is protected, because the alternative ordering tests the control and depends on it in the same run.

**Step 2 (the seed run, one sitting).** If the gate held: point the engine at a **low-stakes real repo** (User-proxy guesses `paradigm-ops` or a scratch fork; **not** `paradigm-agilecards`). Take **5-10 tier-1/2 deterministic-AC cards**, `--pr-gate` on, and Drew reviews all of them in one sitting. That is the KL5 seed and a live gate test in the same run. N=10.

**Step 3.** Everything else re-plans against what those 10 rows say. Not before.

**Rationale, drawn from the positions.** Every other candidate first move was killed by the council itself, mostly by its own proposer. The store decision does not exist. FL-C is moot. The Postgres/RLS work is for a tenant a Drew ruling says will not exist. The measurement dashboard is plumbing over an empty table by its own author's admission. The market framing was disclaimed by Drew's vision doc eight days before the council sat. What is left standing is one uncontested fact: **the gate has never been exercised and the system has never been observed.** The engine is not a well-tested system; it is a well-tested set of parts, tested against fixtures its own authors wrote. The distance between those two is the entire alpha, and one run closes more of it than any amount of schema argument.

Against Drew's principles: this **is** "done properly from the beginning" (you cannot build correctly on a system nobody has watched run) and it is **not** a rewrite of anything. Under the repaired binding test, running the engine is not a decision anything can reverse; it is the input every downstream decision needs.

**The genuine downside, stated plainly.** Three of them.

1. **Success makes the bottleneck worse.** A working engine produces agent PRs Drew must read, and review capacity is the binding constraint. Nobody priced this. Capping at N=10 in one sitting is a mitigation, not an answer.
2. **N=10 may be noise.** The Expansionist says it flips to "more" only past ~30 cards with the gate actually enforcing. Ten rows will not calibrate a quoting model. They will tell you whether the machine runs, which is a different and currently more urgent question.
3. **The run can fail in a way that eats the day.** The gate defect could swallow the run. That is information too, but it is a real cost and it lands on the person with the least time.

**Flip conditions.**
- **If the gate does not block a bad card in the rehearsal**, stop. Do not proceed to Step 2. FL-I is then not a knob-defaults problem, it is a control that does not work, and it is the whole build.
- **If the first 10 rows show human-review minutes per merged PR above Drew's current hand-coded rate**, the quoting story dies and Cards is a toy he dogfoods (User-proxy's own flip, threshold admittedly guessed).
- **If a paying customer asks for the board, or Track P's portal federation lands a second in-house consumer**, FL-D reopens, the incumbents matter again, and Branch A comes off ice.
- **If `CARDS_STORE`/`CARDS_TODO_ROOT` are set, or BROOKFIELD holds a populated store**, verification #4 is wrong and the "never been run" finding collapses. Check this first; it costs a minute.

**Weighing the User-proxy's vetoes explicitly.** Three were issued and all three are upheld:
- **Veto on the Phase 0-4 arc.** Upheld. Phases are calendar cosplay; the unit is PRs against tiered review capacity, and any phase boundary that needs Drew is the boundary that stalls when Mattick or Remi's IEP eats a week. Nothing above is a phase. Step 1 and Step 2 are two runs and a handful of PRs.
- **Veto on two card models.** Upheld, and it is now free: FL-C's collapse plus FL-D means there is only one store in scope.
- **Veto on Fable doing mechanical re-typing at 2x Opus.** Upheld and binding on the build brief. The rehearsal and the seed run are mechanical: config, invocation, observation, log-reading. Fable's job is the call about what the rows mean, not the keystrokes that produce them.

One User-proxy claim does **not** survive and should not be carried forward: its FL1 (b) vote. It was flagged as extrapolation, it was extrapolation, and it was wrong.

---

## 7. Action items

1. **Check the store-location caveat before anything else.** Is `CARDS_STORE` or `CARDS_TODO_ROOT` set? Does BROOKFIELD hold a populated `cards.db`? One minute. If it does, verification #4 is void and this plan re-opens.
2. **Fix CI.** Remove `continue-on-error: true` from the board's 96 tests; make the FastAPI auth suite required. Audit's highest-ROI item, still open.
3. **Commit Branch B's ~15 uncommitted files** to a throwaway branch. They are one `git clean` from gone.
4. **Rehearsal run:** throwaway repo, fabricated cards, `--pr-gate` on. Pass condition: gate observed blocking a bad card, `card_metrics` non-empty. Stop the whole plan if it fails.
5. **Seed run:** low-stakes real repo (not `paradigm-agilecards`), 5-10 tier-1/2 deterministic-AC cards, `--pr-gate` on, Drew reviews all of them in one sitting.
6. **Cut Postgres, `org_id`, RLS, and tenancy from Phase 1.** Run the engine's SQLite. Branch A goes on ice intact (branch preserved, documented as the thaw-on-first-tenant asset), not deleted.
7. **Merge or delete `origin/design/storage-substrate-v2`.** `storage_substrate_v2.md` is referenced ~20 times across the engine and **is not on `main`** (branch is 1 ahead / 81 behind, 2026-05-19). The design doc the entire store rests on was never merged. This is the proximate cause of the whole FL1 fiasco: the memo could not read the design and trusted a stale status doc instead.
8. **Correct the stale docs that caused this.** `AGILECARDS_MVP_LOCALGPU_ASSESSMENT_2026-07-14.md` ("card files are the source of truth") and `metrics/__init__.py:5-7` ("the chunk 2 writer ... is the eventual data source" -- the writer is wired). Mark `01_RECONCILIATION_MEMO.md` and `02_REVISED_HANDOFF.md` as superseded on FL1/FL2 so no future session re-inherits the error.
9. **Change the default, or measure it.** `pr_gate_enabled=False` in three places (`types.py:218`, `doctor.py:138`, `cli/__main__.py:82`) with a silent degrade path and no usage telemetry. After the rehearsal proves the gate, either flip the default or instrument whether it ran. Doc `03` calls jidoka the "STRONGEST MAP"; it is currently resting on an unexercised knob.
10. **Price the review-bandwidth risk before Step 3.** Nobody did. A working engine makes Drew's binding constraint worse, and the seed run's 10 rows are the first and only chance to measure it: log human-review wall-seconds per merged PR (`card_metrics.human_review_wall_seconds` is instrumented for exactly this) and compare it to his hand-coded rate.

---

## Process disclosures (read these before trusting the above)

- **Validation gate: 5/5 PASS in Round 2.** All five rebuttals opened with a valid `ENGAGEMENTS:` block naming real prior-round claims and followed through. No re-fires, no NON-RESPONSIVE flags.
- **Researcher was not degraded.** It had WebSearch/WebFetch and used them; every market claim above traces to a dated primary source.
- **User-proxy was not degraded.** It read memory successfully; DR-9 is quoted, not inferred.
- **Round 3 was targeted by design, not truncated.** Only personas party to live fault lines (FL-C, FL-G/FL-I) were re-spawned. The Logician and Researcher stood on Round 2, whose positions the round's new evidence did not contest. That is the skill's protocol.
- **Two verification caveats, carried honestly.** (i) The "engine has never been run / ledger is empty" finding rests on the absent default store `C:\dev\todo\cards.db`, an empty card tree, and a **maxdepth-3 scan that timed out before completing**. `CARDS_STORE`/`CARDS_TODO_ROOT` could redirect the store and Drew has a second machine (BROOKFIELD). On DREWSPC, the default store is absent. This is action item 1 for a reason. (ii) **No DB-dependent test on either backend branch was actually executed** -- there was no live Postgres in the audit environment. Claims about Branch A's RLS rest on reading its code and tests, not on running them.
- **The process that produced the two false fault lines is still the process.** The reconciliation session could not read other sessions' transcripts and did not grep the engine's store package before making the highest-stakes call in the document. The Contrarian flagged this in R1 and it stands: one agent verifying against `main` before a decision ships is not optional. Two of this council's rounds were spent undoing a ten-minute check nobody ran.
