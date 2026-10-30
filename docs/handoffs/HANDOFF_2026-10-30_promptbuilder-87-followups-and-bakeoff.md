# HANDOFF 2026-10-30: PromptBuilder, #87 follow-ups, bake-off v2 result, v3 groundwork

Seat: PromptBuilder (Claude Opus 5.5). Coordinator: Daedalus. Session
2026-10-29 evening to 2026-10-30 00:45 CDT.

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
  Deep Verify is pending and is Daedalus's.
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

1. **Do not push to #91.** Daedalus's Deep Verify is running there, and
   Daedalus merges it when it passes.
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
