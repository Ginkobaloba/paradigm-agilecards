# Deep Verify: Ginkobaloba/paradigm-agilecards PR #86 (draft, stacked on #85)

Verified-Commit: f4e3c8453a8d597927f621be7fe1ac29005eb0f7

Delta verified: 40ab20ba70489fb27e269133584beaaee2479d4f..f4e3c84 (one commit; 40ab20b confirmed ancestor via merge-base --is-ancestor). `gh pr view 86` = headRefOid f4e3c84..., base fix/secret-isolation. Files: git_safety.py, test_secret_isolation.py, verify/tier3_paths.txt, .github/AUTO_MERGE.md, one ledger entry.
Fresh clone + worktree for probes under scratchpad\verify-ac86. FAKE secrets, fake HOME/USERPROFILE, local bare repos, no pushes, no credential store touched. Real global gitconfig at end: 906 bytes, 2026-09-16 16:06 (unchanged). Layers 3-5 N/A (no deployed surface).

## Verdict: PASS (no blocker; warnings below)

## 1. W1 (branch.<x>.remote value) CLOSED
Probes q1_w1.py (r3 p14 plus variants):
- r3 probe (value = local bare repo with pre-receive hook): hash CHANGED, assert_trusted -> RepoQuarantined, daemon push refused, bare `git push` via the daemon runner refused, hook NOT fired (r3: fired with GH_TOKEN).
- value `origin` (configured): not hashed (no false quarantine; control).
- `ORIGIN` (case differs): hashed. URL value: hashed. `.`: hashed.
- remote added after baseline: remote.*.url hashed -> changed; atomic add of remote + branch.remote=<it>: changed. After re-trust, branch.remote=<that remote name>: exempt (correct).
- remote configured only in an INCLUDED file, with include.path in baseline: value exempt, no change. Fine: include target is now hashed, and git honours the same named remote.
- pushurl-only remote name (no .url): hashed (conservative). `branch.q.remote.sshCommand`, `branch.q.merge.x`, dotted branch names with path value: hashed.

## 2. W2 CLOSED
`[http] sslVerify` (git bool true) vs `sslVerify =` (false) now hash differently. Bare marker is \x01 vs "\n" + value, no collision path for a real key.

## 3. M15 (includes)
- Edit of an unconditional include target (absolute path, relative `rel.cfg`, relative `../../outside.cfg`): hash changes, daemon push refused, plant not fired. Legit unchanged include: no false quarantine; `push -u` twice, second worktree, `--set-upstream-to`: fingerprint unchanged, still trusted.
- includeIf (gitdir pattern and onbranch) target edit: hash UNCHANGED, as declared. With a gitdir condition that matches the repo, git honours the target's core.sshCommand and the daemon push FIRED the plant (GH_TOKEN env available). Declared residual, still real; mitigated only by the same-user threat model. Recommend keeping it prominent in docs.
- Cycles / self-include: git itself exits 128 (include depth); config_entries -> None, raw-bytes fallback of the main file only. No exec risk since git also fails. No hang.
- Device names (NUL, //./NUL -> git fails 128 and None; CON/COM1/AUX/LPT1/CONIN$ resolve relative to .git so are plain missing files; /dev/null fine; C:/Windows dir -> None): no hang, 0.03 s, no exception from trust_repo/fingerprint.
- 200 MB include: fingerprint 4.2 s, no crash (perf note only).
- Differential fuzz p10 (adapted to new signature): 3 seeds x 1500, BYPASS = 0 each (fp equal while git's effective non-daemon keys differ). r2 p9 N1 CR probe unmodified: fingerprint changed, plant not fired, RepoQuarantined. N1 stays closed. p11 targeted matrix all as expected (the two "!!!" lines are the intended W1 change: URL / REMOTE-case values now hash).

## 4. Tests kill M03/M06/M07/M11 (re-applied by me, each over the whole suite, -x)
M03 KILLED (test_an_unparsable_config_is_hashed_as_bytes); M06 KILLED (test_editing_a_value_in_place_quarantines); M07 KILLED (same unparsable test); M11 KILLED (test_no_credential_or_exec_key_is_carried_into_the_tool_profile).

## 5. Tier-3 paths and CI
- verify/tier3_paths.txt and AUTO_MERGE.md both list the same 9 files. Each regex matched exactly one tracked file via git ls-files (git_safety, env_scrub, process_group, spawner, pr_lifecycle, worktree, diff_stats, sdk_invoker, tools). No unintended matches.
- PR #86 CI: Classify PR tier = success; verify-gate log shows TIER3=true, TIER_SOURCE=paths, TIER3_MATCHED=git_safety.py (only changed matching file). Deep Verify (tier-3 PRs only) now RUNS and FAILS: deep_gate.sh finds only a stale report (an unrelated 2026-09-19 one) and requires a committed report under verify/reports/ with a Verified-Commit line pinned to this PR; verify-gate therefore FAILS ("tier-3 PR without a satisfied deep gate"). This is the intended behaviour; it needs this report (or equivalent) committed. Ledger check, Quick Verify, Socket: green. Expect #85 to hit the same gate once its paths are tier-3 via base union.

## 6. Suite and mutants
Fresh clone: pytest 877 passed (94 s), ruff All checks passed. Author claim confirmed. Ledger check job green on the PR.
My 19 mutants on the delta plus #85 regressions (each full suite with -x):
KILLED (16): M03, M06, M07, M11, W1 revert (remote value always exempt), W2 revert (bare == empty), M15 (--no-includes), N1 regression (own line-based parser, CR hides key; killed by the hashed-entries differential test), hooks not hashed (F1), linked config.worktree unhashed, credential.helper reset dropped (F), core.fsmonitor override dropped, core.hooksPath override dropped, GIT_CONFIG_GLOBAL not nulled, all branch.* exempt, pushremote added to daemon-written set.
SURVIVED (3):
- W1b: remote-name match made case-insensitive (value `ORIGIN` exempt when `origin` configured). Real code is correct (probe 3 above); the test suite does not pin it. Impact if regressed: on a case-insensitive FS a worktree dir named ORIGIN containing a bare repo with a hook would be honoured by a remote-less push. Add a mixed-case case to test_a_branch_remote_that_is_a_path_is_hashed.
- W1c: remote-name set built from any remote.<n>.* key instead of .url. Security-equivalent (the attacker-added remote key is itself hashed); test gap only, LOW.
- GIT_CONFIG_NOSYSTEM dropped from the daemon env: equivalent, GIT_CONFIG_SYSTEM=devnull is also set (the tool-env NOSYSTEM mutant was killed in r3).

## 7. Carried residuals (unchanged, declared)
includeIf targets unhashed (now demonstrated to fire with GH_TOKEN when a matching gitdir condition exists); same-user absolute-path writes and Credential Manager reads; first-baseline trust; TOCTOU between check and exec; AST scan is a regression guard not a boundary.

Overall: PASS
