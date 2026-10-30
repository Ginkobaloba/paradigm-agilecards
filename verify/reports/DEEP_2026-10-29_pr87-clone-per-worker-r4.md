# Deep-verify report -- PR #87 addendum r4 (shadow-gate diff `--` fix) -- 2026-10-29

Verified-Commit: 9bc658b377f12b3100ac07a8ba10faecdf752b79
Branch: PR #87 head (paradigm-agilecards)
PR: #87
Operator: independent verifier subagent (Claude), for Daedalus
Environment: Windows 11, git 2.55.0.windows.5, Python 3.13.3, ruff 0.15.22 (pinned), fresh clone under verify-ac87-v4\
Scope: ADDENDUM to r3. Base report: DEEP_VERIFY_pr87_r3.md (3c68c25, PASS). Only code changed since 3c68c25 was re-verified.

## Changed files, 3c68c25..9bc658b (9)
Code: engine/runner/src/cards_runner/daemon/clones.py (+6/-3), engine/runner/src/cards_runner/daemon/diff_stats.py (+17/-5).
Tests: engine/runner/tests/test_deep_verify_fixes.py (+29, two new tests).
Docs/ledger/other: 4 ledger files, engine/RUNNER_CONTRACT.md, verify/reports/DEEP_2026-10-29_pr87-clone-per-worker-r3.md.
Matches the author's claim. No other code changed. test_deep_verify_gaps.py is untouched, so the CI-failing test's assertion is identical to 3c68c25.

## Layers run
| layer | scope | result |
|---|---|---|
| 1-4 | full engine/runner suite 971 passed (282s), ruff check src tests clean | PASS |
| 5 | n/a (no UI surface) | n/a |
| 6 (adversarial) | bug repro, `--` semantics, other-callsite grep, forced-failure logging, 9 mutants | PASS (see notes) |

## Evidence
1. Repro: repo at cwd 190 chars. `git diff --numstat base...sha` -> rc=128 "fatal: failed to stat '<83 chars>': Filename too long" (also with core.longpaths=false; the machine has LongPathsEnabled=1, git still fails). With trailing `--` -> rc=0, "4 0 b.txt". Mutant M1 (remove `--`) is killed by test_diff_stats_read_a_deep_repo with the same rc=128 text.
   Deeper: at cwd 205 git cannot write objects ("unable to write file .git/objects/..: Filename too long": add, commit fail); at 225+ git init fails ("cannot stat", .git/config). So ~250 chars breaks init, clone, add and commit regardless of `--`: an environmental ceiling of roughly 200 chars, not caused or fixable by this PR. At 190 the flow (checkout -b, checkout --detach, rev-parse --verify, ls-tree, symbolic-ref, push sha:ref, commit) all passed. Fetch/clone/push worked at 205 (reads) but object writes fail there. The daemon's clone/commit steps therefore cannot run past ~200 chars of state-dir depth; nothing here guards that (see F2).
2. `--` semantics: ran diff-index --cached, rev-list --parents -n 1, diff-tree -r, diff --numstat A...B, rev-list A..B, each with and without `--` on a small repo: byte-identical output, none became empty. `--` only ends revisions; nothing after it, so no revision is turned into a pathspec. Bonus: when a file named like the sha exists, rev-list without `--` dies "ambiguous argument: both revision and filename"; with `--` it works. So `--` is strictly safer. Bad rev with `--` still fails rc 128 (fails closed in clones, logged in diff_stats).
   Note: at cwd 190 the three clones.py calls do NOT reproduce without `--` (a single 40-char sha stat fits under 260); they are hardening for depths where git already cannot write objects. Not a defect, but those `--` are unreachable-bug fixes.
3. Other git calls taking a rev without `--` (grep of engine/ src): clones.py `checkout --quiet <branch>` (L144) and `checkout --quiet -b <branch> <base_sha>` (L179, L283), `checkout --detach <sha>` (L426), `rev-parse --verify <rev>^{commit}` (L159 uses --end-of-options; L312, L427 HEAD^{commit}), `ls-tree -r <sha>` (L395), pr_lifecycle `push origin <sha>:refs/heads/<branch>` / `origin <branch>`. All run through `_git`/run_git; non-zero rc raises CloneError (fail CLOSED); push returns GhCallResult (caller sees failure). Empirically at cwd 190 all of them succeed without `--` (checkout -b, checkout branch, --detach, rev-parse --verify, ls-tree). Only from_worktree swallowed errors (fail OPEN); it is the sole such site and is now logged. Worker git tool (worker_stub/tools.py) passes model-supplied argv; out of scope.
4. Logging is real: forced failure (branch "no-such-rev") emits `WARNING cards_runner.daemon.diff_stats: diff stats unreadable in <worktree>: git rc=128 fatal: bad revision '<sha>...no-such-rev'`. rc and stderr both present. Gate is shadow-only today: daemon.py L209 builds `ConfidenceGate()` with default config (mode "shadow"); `is_live()` has zero callers in src; `_record_gate_shadow` only calls writer.record_gate_shadow_decision and its docstring/except swallow all errors; no routing or card blocking reads the decision. Confirmed by grep. Author's open item stands: an unreadable diff still returns an empty DiffStats (fail-open if the gate is ever made live).
5. Suite: 971 passed, 0 failed (282s). ruff 0.15.22: all checks passed. Mutants (tests: test_deep_verify_fixes.py + test_deep_verify_gaps.py, baseline 63 passed):
   - M1 drop `--` in from_worktree: KILLED (test_diff_stats_read_a_deep_repo)
   - M2 drop `--` diff-index: SURVIVED
   - M3 drop `--` rev-list: SURVIVED
   - M4 drop `--` diff-tree: SURVIVED
   - M5 remove rc!=0 log: KILLED (test_an_unreadable_diff_is_logged)
   - M6 remove OSError/SubprocessError log: SURVIVED
   - M7 both logs removed (swallow restored): KILLED
   - M8 log omits stderr: SURVIVED
   - M9 log omits rc: SURVIVED
   Tally: 3 killed, 6 survived of 9.

## Findings
- F1 (LOW, non-blocking): M2-M4 survive. The three `--` in `_integrate` have no regression test, and cannot be reproduced at any depth where git still works (see Evidence 1-2). Pure hardening.
- F2 (LOW, non-blocking): M8/M9 survive. test_an_unreadable_diff_is_logged asserts only the substring "diff stats unreadable", not that rc and stderr are present. I verified manually they are (Evidence 4), but a regression dropping them would pass. M6: the exception-path log is untested.
- F3 (LOW, non-blocking, author-declared): unreadable diff still reads as empty (fail-open) while the gate is shadow-only. Must become fail-closed before the gate goes live.
- F4 (INFO): state dirs deeper than ~200 chars break git writes (add/commit) and >~220 break init/clone on this git; nothing in the PR warns about this. Environmental, not introduced by the PR.
- F5 (INFO): new test_diff_stats_read_a_deep_repo is windows_only; on non-Windows the regression is not exercised.

## Notes / follow-ups
- Nothing blocks. Optionally assert "rc=128" and a stderr fragment in the log test, add a caplog test for the OSError path, and track F3 before gate goes live.
- Not exercised: actual GitHub CI run; a long-path-disabled Windows host (this host has LongPathsEnabled=1; git failed anyway); non-Windows; the 29 Dolt-skipped tests (none skipped locally: 971 passed).

Overall: PASS
