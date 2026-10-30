# HANDOFF 2026-10-30: PromptBuilder, #87 follow-ups, bake-off v2 result, v3 groundwork

Seat: PromptBuilder (Claude Opus 5.5). Coordinator: Daedalus. Session
2026-10-29 evening to 2026-10-30 00:45 CDT.

## FIRST TASK NEXT SESSION: #91 Deep Verify FAILED (received 00:56)

Daedalus's independent verify of #91 (product code at `ae68cfa`, the same
as `3319e86`) failed. The report is at
`C:\Users\Drama\AppData\Local\Temp\claude\C--dev\9d799f8e-f7bc-4e2f-ac99-2c7f2f117055\scratchpad\verify-ac91\DEEP_VERIFY_pr91.md`,
with repros in `w\urlwin.py` and `w\urlrace.py` beside it. Read it before
touching code.

- **B1 (high, blocker; already on MAIN through #87, so not a #91
  regression; #91 claimed V3-1 closed):**
  - The race: a sibling worker rewrites the integration clone's
    `remote.origin.url` after `git remote add` and before the second
    `trust_daemon_clone` baseline. It won 40 of 40.
  - The attacker url gets baselined. The push through
    `run_git(keep_gh_credentials=True)` to a local bare repo then runs the
    receiver's pre-receive hook with `GH_TOKEN` in its env. Local transport
    strips `GIT_CONFIG_COUNT`, so the hooksPath override never reaches the
    receiving git.
  - So it's code execution with the push credential. This handoff's
    RUNNER_CONTRACT text ("run no program ... where the integrated commit
    is pushed") is FALSE, and it was PromptBuilder's claim.
  - A plant before the baseline is refused (12 of 12). The hole is exactly
    the window between remote-add and the baseline.
  - Fix, all three:
    (a) push to the url the daemon read from the PROJECT, passed explicitly
        (`git push <url> ...`), never through the clone's remote config;
    (b) read `remote.origin.url` back after `remote add` and compare it
        before baselining;
    (c) refuse non-https and non-ssh push urls (file://, local paths), so a
        local-transport receive-pack can never run.
    Plus a race test that does what the repro did.
- **B2 (high, on the literal claim):**
  - The engine is right: readable=False goes to human_review, and the
    escalator can't be disabled. But the daemon's gate is shadow-only:
    `_record_gate_shadow` changes no routing, and daemon.py:1721-1722 calls
    `_verifier_apply_pass` right after it, unconditionally.
  - A `RepoQuarantined` from `from_worktree` is swallowed by
    `except Exception` with no audit record.
  - The missing-claim-paths fallback is `DiffStats()` with readable=True.
  - Fix: make the claim text say "audited as escalation; routing is
    shadow-only until the gate goes live"; stop swallowing RepoQuarantined;
    make the fallback readable=False. Whether the gate routes at all is a
    separate decision, not this PR's.
- **F3 (medium):** a prep-time GitUnavailable leaves 2 store events and 1
  run dir per poll, without bound, while git is down. Cap or dedupe it.
  (The ledger's "a log line per poll" understated this.)
- **F4 (low):** pin http-alternates with a test, and fix the merge-gate
  reason string.
- **F5 (low):** the short test ids still fail at basetemp 96+, so derive
  the ids from a hash.
- **What passed:** 432 V3-1 plant trials with 0 markers and 0 trusted (the
  positive control on main fired 10 of 12). V3-5, M1-M9 and V3-4 all
  killed. Suite 998, ruff clean, 31 of 34 mutants killed.
- **Process (Daedalus, 00:57):**
  1. **B1 ships FIRST, as its own small PR from main**, ahead of the rest of
     #91. It's the live exposure on main, and a small diff gets a fast,
     focused verify. #91 then rebases on main, or merges main in.
  2. **#91's branch is open again** for B2 and F3-F5 (`fix/runner-87-followups`,
     worktree `ac-87fu`). Its verified V3-1 and V3-5 work stays where it is.
  3. **No real worker runs on the agilecards daemon until the push-url PR
     merges.**
  4. Whoever re-verifies has to be a different session from the author.

## What this session did

- **Windows Update** is now approve-only, with auto-restore after a reboot
  (Drew asked first-hand). The script is `C:\dev\_scripts\set-wu-approve-only.ps1`,
  read back from the registry.
- **#87** (clone per worker) merged after its CI root cause was found: git
  stat()ed a revision range as a path and passed Windows' MAX_PATH. Fixed
  with `--` plus logging.
- **#89** (local map warn or stop): merged main in, CI green; Daedalus merged it.
- **#91** (#87 follow-ups, head `3319e86`): an unread diff now fails closed
  (`diff_unreadable` policy escalator). GitUnavailable is retryable: prep
  rolls back to backlog, and after the worker the card blocks with
  `git_unavailable`, never requeued. V3-1 adds a daemon-clone config
  allowlist and refuses alternates and attributes; V3-5 changes how binary
  deltas are counted. The M2-M4, M6, M8, M9 and V3-4 tests are in, and every
  new test was run against a mutant. CI: engine runner 986 passed, 0 failed.
  Deep Verify FAILED (see the first section).
- **Issue #92** filed (low): card ids are not length-bounded in branch and
  ref paths.
- **Bake-off, exam v2:** A, B, C and E are all valid at 100%, so the exam
  saturates. The write-up is `harness/RESULTS_EXAM_V2.md`. Arm D
  (gpt-oss:120b) was stopped by its commit watchdog 61 s into the model
  load, with commit free going from 69.2 GB to 6.7 GB. No card ran.
- **Exam v3:** the plan is `harness/EXAM_V3_PLAN.md`, revised after a
  dissenting Gemini review, with Daedalus's decisions recorded. The format
  contract for the author is `harness/CARD_FORMAT_V3.md`. **Curie authors v3,
  blinded from PromptBuilder.**
- **Re-score tool** (`harness/rescore.py`, this branch): the v2 baseline is
  144 of 144 rows identical under the arms' own scorer hashes.

## What is currently broken or incomplete

- The v3 harness refactor isn't started. Per Daedalus it starts fresh, on
  this branch.
- Arm D isn't runnable on this machine as configured. It needs about 72.5 GB
  of commit free to load, and Drew decides how to get there (pagefile or
  apps).
- #88 (the bake-off harness and v2 record) is open and needs a targeted
  review before it merges (see below).

## What the next session should do first

1. **#91 Deep Verify FAILED.** See the first section: B1, B2, F3-F5.
2. **The #88 merge** needs a short targeted review of the commits after
   `5c2e640`: `a411fe0` (small fixes), `252d59f` (the flush test), and
   `a2c8a2e`, `a93dff3`, `9b6b022` and `493ae2b` (docs). Ask Daedalus who
   reviews. Independent means a different Claude session from the author.
3. **The v3 refactor** on `feat/bakeoff-v3-harness` (worktree
   `C:\dev\_review-wt\ac-bakev3`, stacked on #88):
   - An `Exam` object loaded from a root, with v2 expressed as "every card's
     seed is `seed_repo`".
   - Per-card seeds, and a held-out overlay with its two mechanical rules:
     refuse a card whose `heldout/<id>/` shares a path with
     `solutions/<id>/`, and record `heldout_overwritten` on the row. Also a
     test that no held-out path appears in the prompt.
   - A separate v3 results dir.
   - The long-context sub-exam: `num_ctx` 65536, `num_predict` 26624, the
     board's max grant per arm, and residency reported per arm.
   - **Before anything else after the refactor:** `rescore.py` must show
     144 of 144 identical under the new hash. If any row differs, stop and
     send Daedalus the rows. Ledger the old hash, the new hash and the count.
4. **The #89 F1-F4 PR**, after #91 lands, as a separate small PR from main:
   - F1: fail closed if the `_record_map_fallback` store write fails.
   - F2: a test that pins the default (`stop`).
   - F3: stamp every billed tier once the cascade escalates.
   - F4: clear a stale `billed_fallback_model` on a later local retry.
5. **Never read `engine/bench/local_bakeoff_v3/`** until Curie sends the
   frozen digest.

## Open questions for Drew

- **Arm D:** grow the pagefile (about 16 GB more), close apps when D runs
  (WSL, Drive, Warp and Bambu Studio hold about 10 GB of commit), or record D
  as "doesn't fit this machine"?
- **A guardrail comparison** for the abliterated model needs a probe set you
  approve. The current two prompts are too mild to separate the models.

## Pointers

- Ledgers: `docs/ledger/2026-10-29-2345-runner-87-followups.md`,
  `2026-10-30-0045-bakeoff-exam-v2-saturates.md`,
  `2026-10-30-0045-bakeoff-rescore-baseline.md`
- Bake-off results (gitignored): `C:\dev\_review-wt\ac-bakeoff\engine\bench\local_bakeoff\harness\results\`
- Arm D's watchdog log:
  `C:\Users\Drama\AppData\Local\Temp\claude\C--dev\4393ba2b-c5ce-4c98-afbd-06137b24caf5\scratchpad\d-commit.log`
- Worktrees: `ac-87fu` (#91), `ac-bakeoff` (#88), `ac-bakev3` (this branch),
  `ac-mapwarn` (#89, merged; it can be removed after checking it's clean).
- Lessons recorded this session (memory): `ntfs-listing-shows-zero-for-open-files`,
  `launch-elevated-scripts-for-drew`. Daedalus recorded
  `ollama-weights-charge-commit-not-mmap` in `gpu-traps`.

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in
this project, then this file, then run `vstart`.
