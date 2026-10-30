# 2026-10-29 21:45 CDT - bake-off: runs and timeout pinned before the arms
- **Who:** PromptBuilder seat (Claude Opus 5.5), on Daedalus's targeted check of #88 at 5c2e640 (READY; 16 of 19 mutants killed, and the gaps it found are fixed here, no re-review needed).
- **Change:**
  - `--runs` other than 3, or `--timeout` other than 1800 s, is listed in `config.off_protocol` and makes the arm invalid. A `--runs 1` arm came back valid with a meaningless pass^3. PROTOCOL.md names a request timeout but no number, so 1800 s is the harness's value, stated in harness/README.md.
  - harness/README.md: the stale line saying pass^3 is over cards with no infra error now describes the headline (errored card as a fail) and the protocol's figure.
  - Tests the check's mutants showed were missing (the code was already right): a None digest read invalidates the arm (N3b); an off-tag model sets `off_protocol` (F8c); plus runs, timeout and a no-flag control.
- **Why:** each setting changes what a number means, so a run off any of them must not report valid.
- **State after:** harness tests 150 pass, ruff clean. No arm has run.
- **Refs:** C:\Users\Drama\AppData\Local\Temp\claude\C--dev\9d799f8e-f7bc-4e2f-ac99-2c7f2f117055\scratchpad\review-ac88-v3\; engine/bench/local_bakeoff/harness/run_bakeoff.py `RUNS`, `REQUEST_TIMEOUT_SEC`; engine/bench/local_bakeoff/harness/test_run_bakeoff.py
