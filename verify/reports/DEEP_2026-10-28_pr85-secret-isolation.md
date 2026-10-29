# Deep-verify report -- agilecards #85 (secrets out of model-run code; daemon git runs nothing planted) -- 2026-10-28

Verified-Commit: 61f02c641ad2230b8687ce1f7487669d5e190c42
Branch: fix/secret-isolation
PR: #85
Operator: Daedalus session (orchestrator), independent verifier subagents in fresh clones; author was the PromptBuilder session
Environment: Windows 11 desktop (DrewsPC), Git for Windows 2.55.0, fresh clones + venvs under the Daedalus scratchpad; FAKE secrets, fake HOME, local bare remotes only

## Rounds

| round | sha | verdict | carried to next round |
|---|---|---|---|
| r1 | 96df723 | FAIL | F1 daemon worktree git unhardened (HIGH), F2 global gitconfig not neutralized (HIGH), F3 baseline re-taken at claim, F4 no re-check after verifier ACs, F5 USE_TOOLS env bypass, F6 no-baseline mutant survives, F7 --track false block, F8 TEMP hooks-dir race, F9/F10 low |
| r2 | 66840c2 | PASS WITH WARNINGS | N1 lone-CR fingerprint parser differential (MED-HIGH, demonstrated), N2 Credential Manager via system git helper (declare residual + NOSYSTEM), scan pin evadable, R18/R30 test gaps |
| r3 | 61f02c6 | PASS WITH WARNINGS (no blocker) | W1 branch.<x>.remote value unhashed (latent), W2 bare key vs empty value, W3 perf; surviving mutants M03/M06/M07/M11 (test gaps). All to a follow-up PR; no code pushed to #85 after r3 |

Accepted residuals (declared in the PR body): same-user processes are not isolated (process memory, absolute-path writes, Windows Credential Manager reads, a PATH plant before daemon boot); tool shells have network access; an existing include.path target's contents are unhashed; the first baseline is only as clean as the repo at first sight; the verifier's shell ACs run under the daemon (second seam). The structural fix is the queued R1/R2 work (throwaway clone, patch returned as data).

Overall: PASS

## Round 3 report (verbatim)

# Deep Verify r3: Ginkobaloba/paradigm-agilecards PR #85 (draft)

Verified-Commit: 61f02c641ad2230b8687ce1f7487669d5e190c42

Range 66840c2..61f02c6. `gh pr view 85 --json headRefOid` = 61f02c641ad2230b8687ce1f7487669d5e190c42; `git rev-parse HEAD~1` = 66840c215de71bbab6abbea1ef03a36c16c8a5b1 (parent confirmed).
Fresh clone: scratchpad\verify-ac85\r3\clone (mutation target) and r3\clone_probe (probes). Probes: r3\probes and r3\pp. FAKE secrets, fake HOME/USERPROFILE everywhere, local bare repos only, no pushes, no real credential store touched. Real C:\Users\Drama\.gitconfig at end: 906 bytes, 2026-09-16 16:06 (unchanged). Layers 3-5 N/A (no deployed surface).

## VERDICT: PASS WITH WARNINGS (no blocker)

N1 (r2 lone-CR fingerprint bypass) is CLOSED. No fingerprint bypass found despite targeted probes and ~17,000 differential fuzz pairs. Warnings: W1 (unhashed branch.<x>.remote value is honoured by any remote-less push/fetch/pull; demonstrated at git level, not reachable through today's daemon calls), plus test gaps shown by surviving mutants.

## 1. N1 re-run (the r2 probe, unmodified except the sys.path line)

r2\probes\p9_cr_exploit.py run as-is against 61f02c6 (only the import path changed to the r3 clone):
```
fingerprint unchanged by edit: False
git effective core.sshCommand: sh 'C:/.../ssh.sh'     (git does honour the planted command: positive control)
daemon push ok: False | PLANT FIRED: False
assert_trusted afterwards: RepoQuarantined
```
At 66840c2 the same probe fired the plant with GH_TOKEN and left the repo trusted. Now: quarantined, push refused, no plant. CLOSED. The author's rewritten test (test_a_lone_cr_cannot_hide_an_exec_key_from_the_hash) is in the suite and passes; I did not rely on it.

## 2. New design attacks (fingerprint = `git config --file f --no-includes --null --list` minus branch.*.remote/merge/gh-merge-base; unparsable file hashed as raw bytes)

Differential oracle: real git's effective config in the repo (`git -C proj config --list -z --no-includes`, null global/system) vs fingerprint. p10_diff.py: random 1-4 insertions of hostile tokens (lone CR, CRLF, BOM, NUL, VT/FF/FS/NEL/U+2028, fake [branch]/[core]/[CORE] headers, escaped quotes, continuations, `[branch.q]`, include) into the config; 4 seeds x 4000 trials: 15,700+ fingerprint changes, ~3,500 parseable divergences from baseline, BYPASS = 0 in all seeds (fingerprint equal while git's effective non-daemon keys differ). p3 fuzz (r2 harness) on the same code: 1915 parseable pairs, 0 bypasses.

Targeted (p11_targeted.py), expected vs actual all OK:
- (a) include ADDED: `[include] path=` and `[includeIf]` change the hash (include.path is an entry). Unhashed include TARGET edit still fires core.sshCommand with GH_TOKEN (p2 "RESIDUAL", declared); the same edit cannot revive a credential.helper (reset).
- (b) branch.x.remote naming a new remote `evil` requires `[remote "evil"]` keys, which are hashed: changed. branch.x.pushRemote, branch.*.sshCommand, `[branch.z]` legacy syntax, escaped-quote subsection `[branch "a\"b"]`: all hashed/changed. Case variants of the daemon-written keys (`REMOTE`, `Merge`) are lowercased by git and correctly excluded. See W1 for the value of branch.x.remote.
- (c) unparsable file: `[bad` and NUL give entries None and the raw-bytes fallback; two different bad edits hash differently. I could not craft a file that `git config --file` refuses but the repo-mode git accepts (fuzz and targeted). The fallback is untested by the suite (M03/M07 survive, see mutants).
- (d) CRLF whole-file and BOM: hash unchanged (git ignores both; correct, same semantics). 5 MB comment line: unchanged, 2 MB value line: changed, no timeout. Duplicate `[core]` with override, `[CORE] SSHCOMMAND`, `[remote "Origin"]` (subsection case is significant): all change the hash. Lone-CR variant: changed.
- (e) config.worktree: main `.git/config.worktree` and linked `.git/worktrees/<id>/config.worktree` both hashed. With extensions.worktreeConfig=true trusted, a planted linked config.worktree sshCommand (git sees it) quarantines and the daemon push does not fire.
- Legit flow (p3): add, commit, checkout -b, branch --track, daemon `push -u` (twice), diff_stats, next card prepare: no false quarantine; daemon-written keys do not self-quarantine.
- F1-F5 re-run (p1, p2 matrix, p5): hooks/fsmonitor/shared sshCommand/receivepack/pushurl/credential.helper plants quarantined; global-config plants do not fire; F4 verifier-rewrite quarantines; config read forms refused; boot-pinned git survives a PATH plant. No regression.

### W1 (LOW-MEDIUM, latent, demonstrated at git level only): branch.<x>.remote VALUE is unhashed
Exempting `branch.<name>.remote/merge` wholesale hides the VALUE, not just the daemon's write. p14_branchremote.py: set only `branch.card/x.remote = <local bare repo with a pre-receive hook>` and `.merge`; fingerprint unchanged. The daemon's explicit `git push origin card/x` is unaffected (fired=False), but a remote-less push run through the daemon's own runner (`git -c push.default=current push`) honours the attacker path and the pre-receive hook FIRED (daemon env, GH_TOKEN present). Today's daemon calls: push is always explicit `origin`; diff/worktree add/prune/remove take no remote; gh pr create/merge/view address the remote explicitly (not run end to end here: would need real network, so unverified). Not exploitable through current code, but any future bare push/fetch/pull, or a gh behaviour that follows branch.<x>.remote, reopens F2-class execution with credentials. Cheap fix: exclude a branch.*.remote entry only when its value is a configured remote NAME (a key `remote.<value>.url` exists in the same file); hash anything else (URL/path). Also a regression test that a branch.*.remote of a path changes the hash.

### W2 (LOW): bare key and empty value collide
`--null` prints a bare key as `key\0` and `key =` as `key\n\0`; `record.partition("\n")` maps both to "". Verified: `[http] sslVerify` (true) and `sslVerify =` (false) give identical fingerprints. Only boolean flips are invisible (no known program-launching boolean; security-relevant example http.sslVerify). Fix: keep whether the separator was present.

### W3 (LOW, perf): one git spawn per config file per run_git
Each daemon git call now runs `git config --file` for config, config.worktree and every linked worktree's config.worktree. Measured 0.016 s with no worktrees, about 1 s with 20 linked worktrees each holding a config.worktree (under load). Only worktrees that have a config.worktree file are counted. Acceptable; note for max_parallel growth.

### Include residual could be closed cheaply
Mutant M15 (drop `--no-includes`, so include targets are expanded into the listing and therefore hashed) SURVIVES the suite and is behaviourally stronger, not weaker. If the include-target residual should go, that is a one-flag change plus a test; legit repos with includes whose targets change would then quarantine.

## 3. Tool env and credentials

p12_profile.py with a hostile fake "real" global config (user.name `Eve "Q" \ [core] sshCommand=x`, user.email with embedded newline and `[credential] helper = !evil`, plus credential.helper and core.sshCommand globally):
- Result .gitconfig is written by `git config --file`, so values are escaped: both values round-trip exactly through `git config --list -z`; no injected section; credential.helper and core.sshCommand are not carried; only listed keys (user.*, core.autocrlf/symlinks, init.defaultBranch) appear.
- Env after `_isolate_profile`: GIT_CONFIG_NOSYSTEM=1, GCM_INTERACTIVE=never, GIT_TERMINAL_PROMPT=0, GIT_CONFIG_SYSTEM/GLOBAL absent, HOME/USERPROFILE under the throwaway dir.
- `git credential fill` with a FIXTURE system config naming a shell helper that prints a fake password: control (no NOSYSTEM) reached the helper (FAKELEAK returned); with the tool env, even with GIT_CONFIG_SYSTEM pointing at the fixture, the helper is NOT reached ("terminal prompts disabled"). I did not run the real GCM against the real store. Residual unchanged: a same-user process can still CredRead the Windows store directly (r2 N2): needs OS isolation; declare it.
- Test pin test_the_tool_shell_does_not_read_the_system_git_config checks the same property and passes.

## 4. R30 / scan pin / locks.py
- R30 test (test_the_daemon_push_keeps_its_github_credentials) asserts GH_TOKEN kept, Anthropic key absent, and credential.helper values == ["", '!"<gh>" auth git-credential']. Mutants M12 (gh helper pin dropped) and M13 (GH_ENV_KEEP emptied) are both KILLED. R30 closed. Caveat: it mocks subprocess.run, so it pins the env contract, not a live auth.
- AST scan pin: the 17 parametrized shapes pass in the suite. Of 23 extra shapes of mine (p13_scan.py), caught: importlib.import_module, `from subprocess import *`, getattr(os,'sys'+'tem'), vars(subprocess), exec(compile()), `from os import popen`, os.execl. MISSED (as expected from a static scan): builtins.__import__, `__builtins__['__import__']`, `__builtins__.__import__`, sys.modules['subprocess'], `from importlib import import_module` (bare call), posix.system, nt.system, os.__dict__['system'], types.FunctionType, _posixsubprocess, runpy, webbrowser, `i = __import__; i(...)`, sys.modules.get. LOW and inherent: a regression guard, not a boundary. Easy cheap adds: bare-name import_module, `posix`/`nt` modules, `__builtins__`/`sys.modules` references.
- common/locks.py allowlist: justified. ctypes is imported inside pid_alive() on win32 only, calling kernel32 OpenProcess/GetExitCodeProcess/CloseHandle; no launch. Note the allowlist exempts the whole file, so a future launch added there would not be scanned.

## 5. Suite and mutants
- Fresh clone: `pytest -q` 871 passed (82 s), `ruff check .` (0.15.22) All checks passed. Author claim confirmed.
- My mutants on the r3 diff (18; each run with `pytest -x` over the whole suite):
  KILLED (12): M01 exclude all branch.* keys; M02 pushremote added to daemon-written; M04 linked config.worktree unhashed; M05 main config.worktree unhashed; M08 suffix match hides pushRemote; M09 NOSYSTEM removed; M10 GCM_INTERACTIVE removed; M12 gh helper pin dropped; M13 GH token scrubbed for daemon; M14 carried values written unescaped; M17 daemon-written exclusion disabled (self-quarantine); M18 NUL record split broken.
  SURVIVED (6): M03 unparsable fallback stops hashing raw bytes; M06 values dropped from the hash (key-only; the code does detect value edits, p16, but no test edits a value in place); M07 git parse failure swallowed as an empty list (so an unparsable file's edits go unseen; no test covers the fallback); M11 credential.helper added to the carried keys (tool shell would regain a helper; no test asserts the carried set excludes credential keys); M15 `--no-includes` dropped (stronger, see above); M16 config_entries run with the unscrubbed env (equivalent: `--file` reads only that file).
  Real test gaps to close: M03/M07 (fallback), M06 (value-only edit), M11 (credential keys never carried). M15/M16 are not defects.

## 6. Carried forward, unchanged
Residuals from r2 still hold and are declared: unhashed include target edit, same-user absolute-path writes and Credential Manager reads, first-baseline trust and `trust-repo` as a same-user primitive, TOCTOU between check and exec, gh not pinned if absent at boot, `python -m cards_runner.daemon` not popping CARDS_RUNNER_USE_TOOLS (pair still unreachable).

Reproduce: venv r2\venv\Scripts\python.exe with PYTHONPATH set to r3\clone_probe\engine\runner\src and HOME/USERPROFILE pointed at r3\fakehome. Probes: r3\probes (p10 diff fuzz, p11 targeted, p12 profile/credential, p13 scan, p14 branch.remote, p15 perf, p16 value edit, mutants_r3*.py with logs), r3\pp (p1/p2/p3/p5 re-runs).

Overall: PASS

## Round 2 report (verbatim; credential target names redacted)

# Deep Verify r2: Ginkobaloba/paradigm-agilecards PR #85 (draft)

Tested sha: 66840c215de71bbab6abbea1ef03a36c16c8a5b1 (gh pr view headRefOid equals it; r2 = ffea914..66840c2 on r1 96df723)
Fresh clone: scratchpad\verify-ac85\r2\clone (+ clone2 for scan probes). Probes: scratchpad\verify-ac85\r2\probes. FAKE secrets only; fake HOME/USERPROFILE for every probe; local bare repos only; no pushes. Real ~/.gitconfig verified unchanged at the end (906 bytes, 2026-09-16 16:06). Layers 3-5 N/A (no deployed surface).

## VERDICT: PASS WITH WARNINGS (at 66840c2)

All six round-1 bypasses (F1-F6) are closed or detected, with evidence below. Two new findings from this round (N1, N2) are real and should be fixed before anyone relies on the fingerprint as the only control or enables tools mode with credentials. N1 is a working hash bypass (demonstrated). N2 is a same-user credential-store read that no code in this PR can close (propose adding to the declared residuals).

## Round-1 probes re-run

| Item | r1 | r2 evidence | Result |
|---|---|---|---|
| F1 hook (reference-transaction, post-checkout) / core.fsmonitor planted in shared repo, fires on next card's prepare_worktree | fired, saw GH_TOKEN + ANTHROPIC key | p1_worktree.py: fired=False for all three; next prepare raises RepoQuarantined; card C, reaper teardown/prune also refused (teardown falls back to rmtree, prune skipped) | CLOSED |
| F2 global ~/.gitconfig sshCommand / receivepack / pushurl / credential.helper / url.insteadOf | all fired or redirected | p2_push.py: GIT_CONFIG_GLOBAL=nul; none fire (control fires for each); `git config --global` from the tool shell lands in run\tool-home\.gitconfig, fake real HOME untouched | CLOSED |
| F3 tampered baseline accepted on next claim / restart / orphan reclaim | accepted | after plant: card C refused; configure() again (restart) still refused (persisted flag); store file deleted while running still refused (memory authoritative). A planted baseline is never re-taken | CLOSED |
| F4 verifier AC rewrites .git/config after exit check | unnoticed | p5_misc.py: verifier passes, sshCommand written, then DiffStats.from_worktree raises RepoQuarantined, push returns refused, run_git status raises (check is before EVERY call) | CLOSED |
| F5 CARDS_RUNNER_USE_TOOLS env bypass of sdk-tools + --pr-gate | rc 0, gate on | p7_f5.py: env in {none,1,true,TRUE,yes,on," 1 ",0} x invoker sdk -> env popped, USE_TOOLS never reaches spawner; pair is rc 2 for sdk-tools. Only source of pr_gate_enabled is the CLI flag. | CLOSED (see info I1) |
| F6 mutant M10 (no baseline) survives suite | survived | equivalent R10 (post-exit check disabled) and R09 (no check in run_git) KILLED by test_merge_gate / _prepare_and_spawn tests | CLOSED |
| F7 `git branch --track` false-blocks card | blocked | p3: add, commit, checkout -b, branch --track, daemon push -u, second push, diff_stats, next card prepare all succeed, assert_trusted OK | CLOSED |

## Author claims

1. One runner: CONFIRMED for the current source. grep of the whole package for subprocess/os.system/Popen/asyncio/shutil.which: only git_safety.py (runner), process_group.py (worker spawn), dolt_store.py, verifier deterministic.py (declared second seam), doctor.py (--version), worker_stub (executor's own tools; sdk_invoker runs `git config --global --get user.*` as the worker). No bypass today. WEAKNESS in the pin: the AST scan only matches `subprocess.X`/`os.X`/`asyncio.X` with a Name owner; I proved it MISSES `from subprocess import run`, `import subprocess as sp`, `os.spawnl`, `os.execvp`, `getattr(subprocess,"run")` (scan_evasion.py). Only the control spelling is caught. LOW. Runtime test covers 4 call sites.
2. Null-device global/system + credential reset: CONFIRMED. Reset is a command-line (GIT_CONFIG_COUNT) entry, so it follows every file, including an edited include target: p2 "include target edited -> credential.helper" does not fire. A repo-level helper planted in shared config is quarantined first.
3. Baseline: CONFIRMED with ONE BYPASS (N1 below). Persisted, first-touch, never re-taken, checked before each call, quarantine survives restart and store deletion while running. [branch] hiding of semantic keys: a hidden `[branch "card/x"] remote/pushRemote=attacker` section does not redirect the daemon's explicit `push origin`, and a [remote] section is hashed. But the stripper itself has a parser differential (N1).
4. Tool-shell profile: CONFIRMED for HOME, USERPROFILE, APPDATA, LOCALAPPDATA, XDG_CONFIG_HOME, HOMEDRIVE/HOMEPATH (all under run\tool-home); GIT_CONFIG_*, GH_CONFIG_DIR, GH_TOKEN, ANTHROPIC_API_KEY, SSH_AUTH_SOCK absent. TEMP/TMP stay the real temp (kept by scrub; low). But see N2.
5. USE_TOOLS only via CLI: CONFIRMED for `cards-runner start`. `python -m cards_runner.daemon` (daemon/__main__.py) does NOT pop it, but that entry has no --pr-gate flag, so the dangerous pair is unreachable (I1).
6. Extra holes: trust store deletion cannot re-arm in a running daemon (R07 killed). Repointed .git file to an untrusted gitdir: UntrustedRepo. Fresh attacker `.git` dir in worktree: UntrustedRepo. `.git` removed with an attacker repo in the parent run dir: git reports not a repository, plant did not fire (ceiling works). Repoint to a gitdir whose commondir is the trusted repo plus a planted config.worktree: ran, plant did NOT fire (worktreeConfig extension not enabled). Binaries pinned at configure(): a PATH plant after boot is ignored (git --version returned the real 2.55). git config reads with --global/--system/-f/--file/--blob refused; `--worktree --get`, `--list --includes` allowed (reads). diff_stats raises on quarantine.
7. Suite 842 passed, 0 failed (76s) in the fresh clone; ruff 0.15.22 "All checks passed". Author's 18 mutants not independently listed; my own 31 below.

## Open findings

N1. MEDIUM-HIGH (demonstrated). Fingerprint parser differential. `_strip_branch_sections` uses `str.splitlines()`, which splits on a lone CR (and \x0b, \x0c, \x1c-\x1e, \x85, \u2028/9); git only splits on \n. An executor appends `\r[branch "z"]` to the LAST line of a section with a free-form value, then adds exec keys on following lines: the scanner thinks a branch section started and hides them, git treats them as keys of the earlier section. p9_cr_exploit.py: repo with `core.sshCommand = ssh -o BatchMode=yes` as last line of [core] (git appends there, so any repo where the owner set a per-repo ssh command): after the edit fingerprint is UNCHANGED, git's effective core.sshCommand is the planted command, the daemon push fired the plant WITH GH_TOKEN in env, assert_trusted still passes (no quarantine). p8: the `fetch` line variant is also hash-invisible (receivepack visible to git) but the corrupted refspec makes push die, so it is not exploitable on a default layout. Needs: a free-form last line in an exec-bearing section. Reachable only when the PR gate and tool mode are combined (refused today) or by other writers; Drew's manual git is not protected by it anyway. Fix: split on "\n" only (`text.split("\n")`), or hash raw bytes and exclude only exact `[branch "..."]` blocks parsed the way git does; add a fuzz/differential test (my 20000-pair fuzz found 135 differential cases, most benign `[branchx]` noise, plus the CR case). Mutant R08 (branch skip never ends) SURVIVES the suite, so the un-skip behaviour is untested too.

N2. HIGH impact, same-user residual, pre-existing (not a regression of r2) but UNDECLARED. The throwaway HOME does not stop credential-store reads. The tool shell still gets Git for Windows' SYSTEM gitconfig (`credential.helper = manager`, origin C:/Program Files/Git/etc/gitconfig) because GIT_CONFIG_NOSYSTEM is not set for the tool env. p6_credstore.py: with a FAKE credential stored for a fake host, the throwaway-HOME tool shell ran `git credential fill < q.txt` and got the fake password back. On this host Credential Manager holds several code-hosting and registry entries [target names redacted in this public copy] (target names only seen via cmdkey; I did not read any real secret). Setting GIT_CONFIG_NOSYSTEM for the tool env stops the git helper path only; any same-user process can still CredRead the store (python/ctypes, PowerShell), so this needs OS isolation. Recommend adding to the declared residuals and, cheaply, setting GIT_CONFIG_NOSYSTEM=1 plus GCM_INTERACTIVE=never in the tool env.

Lower:
- L1 LOW. Scan pin evadable (see claim 1). 
- L2 LOW (false block). sibling_reviewer calls `gh.merge_pr(paths.todo_root, ...)`; if todo_root is itself a git repo root, run_git refuses it (UntrustedRepo, never trusted): auto-merge silently fails with "refused". Fail-closed, config dependent. Probe in transcript (vp_todo_repo).
- L3 LOW. Race/TOCTOU remains by design (check then exec; an executor concurrent with another card's daemon git, max_parallel>1, can flip config in the gap). In tool mode these calls carry no GH credentials (gate refused), so the gain is scrubbed-env user-level execution, the same as the shell already has. Also the nohooks dir sits in the state dir under todo_root, re-emptied per call (tested), but a concurrent same-user write between emptying and exec is a race.
- L4 LOW. gh is pinned only if present at boot; if absent at boot, a gh.exe planted on PATH later would be resolved (resolved() falls through). Narrow (needs gh absent).
- I1 INFO. `python -m cards_runner.daemon --invoker sdk` honors ambient CARDS_RUNNER_USE_TOOLS (no pop), so "CLI is the only switch" is true for `cards-runner start` only; the pair stays unreachable because that entry has no --pr-gate.
- I2 INFO. `cards-runner trust-repo` is a ready-made same-user primitive for an executor to re-record a tampered baseline for the NEXT daemon start (same class as writing git_trust.json; declared). Store deleted AND daemon restarted re-arms first sight (p1 column F: no exception): same declared class.
- I3 INFO. Declared residuals confirmed live: unhashed include.path target edit fires sshCommand with GH_TOKEN (p2, "RESIDUAL include target"), but the same edit cannot revive a credential.helper (reset).

## Mutants (31 of mine, full suite with -x, r2 clone)
KILLED (28): R01 worktree.py bypasses run_git (scan test), R02 no GIT_CONFIG_GLOBAL, R03 no GIT_CONFIG_SYSTEM/NOSYSTEM, R04 credential reset removed, R05 re-baseline on mismatch, R06 quarantine flag ignored, R07 memory not authoritative, R09 run_git skips baseline check, R10 post-exit check disabled, R11 CLI stops popping USE_TOOLS, R12 pr-gate refusal removed, R13 tool shell keeps real profile, R14 ceiling dir removed, R15 binaries not pinned, R16 diff_stats swallows quarantine, R17 hooks not fingerprinted, R19 config read modifiers widened, R20 unknown repo treated as first sight, R21 #84 passthrough set widened, R22 #84 pin missing file not refused, R23 #84 base-URL allowlist off, R24 #84 pin fail-closed off, R25 daemon worktree calls keep GH creds, R26 gh helper not re-pinned, R27 fsmonitor override removed, R28 hooksPath override removed, R29 nohooks not re-emptied, R31 branch sections not excluded.
SURVIVED (3): R08 branch strip never un-skips (hides every later section; ties to N1), R18 config.worktree not fingerprinted (latent: needs extensions.worktreeConfig), R30 push runner drops keep_gh_credentials (functional regression: pushes would lose gh auth, no test).
Plus my own live probes as oracles: p1 (F1/F3), p2 (matrix, 16 plants), p3 (legit flow, branch hide, fuzz), p5 (F4, redirects, pin, config reads), p7 (F5), p8/p9 (N1), p6 (N2).

## Legit-card end to end
add, commit, checkout -b, `branch --track`, daemon `push -u` (twice) to a local bare repo, diff_stats, then the next card's prepare_worktree: all pass, no false quarantine. Only false block found: L2.

## Residuals noted, not failed
same-user absolute-path writes, process memory, pre-boot PATH plant, network egress, unhashed include.path target, first-baseline trust, verifier shell ACs under the daemon.

## Reproduce
venv: r2\venv\Scripts\python.exe; probes in r2\probes (p1_worktree, p2_push, p3_branch_and_legit, p4_fuzz, p5_misc, p6_credstore, p7_f5, p8_cr_hide, p9_cr_exploit, scan_evasion, mutants_r2). Set HOME/USERPROFILE to a scratch dir first.

## Round 1 report (verbatim)

# Deep Verify: Ginkobaloba/paradigm-agilecards PR #85 (draft)

Tested sha: 96df7236628974e3205538bdcf4295c68b383a01 (PR head confirmed equal; base main 3218090)
Mode: deep, layers 1 (code) and 2/6 (runtime + adversarial/edge) executed locally. No deployed surface, so layers 3-5 (network, headless, headed) are N/A and said so here, not silently skipped.
Fresh clone: scratchpad\verify-ac85\clone. Probes: scratchpad\verify-ac85\probes. All secrets used were FAKE. No pushes to any remote (local bare repos only). A fake HOME/USERPROFILE was used for every probe that writes git global config.

## VERDICT: FAIL (at 96df723)

The env-leak half (claims a, b, e as literally stated for `--invoker sdk-tools`, d, g) holds up. The git-execution half (c1/c2) does not meet the stated threat model: a model with the shell tool can still get its code run by the daemon, with GH_TOKEN and ANTHROPIC_API_KEY in the environment, and can leave persistent config that fires on Drew's later manual git. These are open bypasses, listed below with severity.

## Layer 1: code
- 817 tests pass in the fresh clone (75s). Matches the author's number.
- `ruff check .` clean (0.15.22 pinned).
- Author's own tests are real: positive controls fire on this host (Git for Windows 2.55, sh hooks fire).

## Claim-by-claim

| Claim | Result | Evidence |
|---|---|---|
| (a) key popped; belt uses fresh scrub | CONFIRMED | pa_worker.py: real `SdkInvoker.from_env()` then real `shell` and `git` tools with ANTHROPIC_API_KEY, GH_TOKEN, GITHUB_TOKEN, GH_ENTERPRISE_TOKEN, OPENAI_API_KEY, AWS_SECRET_ACCESS_KEY, CARDS_RUNNER_SECRET_X, lowercase `anthropic_api_key_lower` and `gh_token`, MY_PASSWORD, DATABASE_URL-with-creds in env. Shell `set`, PowerShell `Get-ChildItem Env:`, `[Environment]::GetEnvironmentVariable` (process and Machine): none present, no value substring present anywhere in any output. Surviving names are only platform essentials (APPDATA, USERPROFILE, HOME, PATH, TEMP, ...). Parent (worker) command line read via Get-CimInstance Win32_Process: no secrets (python -m style invocation). `wmic` absent on this host. Key is not written to logs or result files by code reading (grep of worker_stub/providers). Process-memory reads out of scope (residual). |
| (b) key only if needed | CONFIRMED | author tests + my mutants M03/M04/M05 killed. Malformed pinned map (`tiers: []`) returns False (no key, no crash). |
| (c1) hardened env for pr_lifecycle._run and diff_stats | PARTIAL: scrub works, config overrides cover only the 5 named keys | see matrix below |
| (c2) git_exec_surface fingerprint | PARTIAL: detects shared-config/hook edits made during the worker run; does not protect the daemon | see findings F1-F4 |
| (d) git tool config limited to reads | CONFIRMED | 19 config forms tried through the real tool: all write forms refused (`-f`, `--file`, `--global`, `--add`, `--unset`, `--replace-all`, `--edit`, `--default`, `--blob`, `config set`, `config get --file`). No file created. `git -c ...` and `--git-dir=` as first arg refused (verb parse). Note `status -c x` passes harmlessly. Weakness: the tool is defence in depth only, `shell` runs any git (author says so). |
| (e) sdk-tools + pr-gate rc 2 | CONFIRMED for the literal flag pair, BYPASSED via ambient env (F5) | |
| (f) planted pre-push via .git/hooks and core.hooksPath | CONFIRMED | control fired (True), daemon push did not fire (False) for both. Also verified c2 sees both. |
| (g) textconv under --numstat | CONFIRMED | flags `--no-ext-diff --no-textconv` present, pinned by test, mutants M08 and M18 killed; planted textconv does not fire. |

### c1/c2 bypass matrix (push path = `SubprocessGhRunner.push`, real function, local bare remote, FAKE GH_TOKEN in env)
control = plain git fires the plant on this host. daemon = fires under the hardened daemon push. c2 = fingerprint changes.

| Plant | control | fires under daemon push | c2 detects |
|---|---|---|---|
| hooks/pre-push | yes | no (blocked by c1) | yes |
| core.hooksPath | yes | no (blocked by c1) | yes |
| core.sshCommand, shared .git/config | yes | YES (plant saw GH_TOKEN in env) | yes |
| core.sshCommand, GLOBAL ~/.gitconfig | yes | YES (saw GH_TOKEN) | NO |
| remote.origin.receivepack, shared | yes | YES (saw GH_TOKEN) | yes |
| remote.origin.receivepack, GLOBAL | yes | YES (saw GH_TOKEN) | NO |
| include.path in shared config -> sshCommand | yes | YES | yes (the include line itself) |
| edit of a file already include.path'd (global or shared) | yes | YES | NO (included file not hashed) |
| remote.origin.pushurl -> attacker repo, shared | n/a | YES (branch landed on attacker repo) | yes |
| remote.origin.pushurl -> attacker repo, GLOBAL | n/a | YES | NO |
| credential.helper with `!cmd` | not exercised live: my http 401 fixture hung because the system gitconfig's GCM helper (stripped from my control env by GIT_CONFIG_NOSYSTEM, but not from the scrubbed daemon env) went interactive. Same class as sshCommand: not in the override list. | untested | yes if in shared cfg; NO if global |
| url.X.insteadOf (GLOBAL) | n/a | inconclusive (my pattern did not match the clone URL spelling) | NO |
| .git file gitdir redirect to attacker dir | n/a | not run through push | YES (fingerprint hashes resolved paths); restoring the file restores the fingerprint |
| planted git.exe in worktree dir, or as parent cwd | n/a | NOT hijacked (Python/Windows did not resolve git from cwd; copy of cmd.exe named git.exe was ignored) | n/a |
| core.fsmonitor / diff.external / textconv / askPass | n/a | overridden by GIT_CONFIG_COUNT (author control verified) | yes |

c1 is therefore a denylist of 5 keys, as the author's module doc admits ("a list of dangerous keys is never complete"). The second layer (c2) is what is supposed to catch the rest, and has the holes below. These push-path rows matter today only if the PR gate runs on a worktree the executor wrote to, which F5 shows is still reachable.

## Open bypasses (severity)

F1. HIGH. The daemon's own unhardened git calls run planted hooks and config with the daemon's full environment.
`daemon/worktree.py` (`_run_powershell_git`: `branch`, `worktree add`, `status`, `worktree remove --force`, `worktree prune`, `worktree list`) still uses the inherited daemon env, no GIT_CONFIG overrides, no scrub. Proof (pg1_worktree.py, real `prepare_worktree`, FAKE GH_TOKEN and ANTHROPIC_API_KEY in daemon env): a `reference-transaction` hook, a `post-checkout` hook, and a `core.fsmonitor` value planted in the SHARED repo all fire on the next card's `prepare_worktree`, and the hook process sees both GH_TOKEN and ANTHROPIC_API_KEY. c2 blocks only the card that did the tampering; the daemon keeps claiming, does not repair or quarantine the repo, and every later claim runs these calls. This is goals (1) and (2) of the threat model, in `--invoker sdk-tools` without any PR gate. Also unprotected the same way: reaper teardown/prune. Variant: a sibling card's prepare_worktree can fire the hook while the executor is still running, and the executor can remove it again before exit, so c2 never sees it.

F2. HIGH. Global/user/XDG git config is neither fingerprinted nor neutralized. The real `shell` tool, with the belt env, wrote `git config --global core.sshCommand ...` into the (fake) HOME (HOME and USERPROFILE are in the scrub keep-list); c2 fingerprint unchanged. Effects shown in the matrix (global sshCommand, receivepack, pushurl all fire or redirect under the hardened push with GH_TOKEN visible, c2 silent). It also persists into Drew's later manual git use, which the PR text says c2 protects. Not caught by any test.

F3. MEDIUM. Tamper baseline is whatever the repo looks like at claim. After card A is blocked, the plant stays; card B's claim fingerprints the already-tampered repo as its baseline and B passes c2 (pg1 G3: B_baseline detects nothing). Same for orphan reclaim or daemon restart (the in-memory handle and its fingerprint are lost, new claim re-baselines).

F4. MEDIUM. TOCTOU after the c2 check. The check happens once at worker exit, then the verifier runs card-specified shell ACs (typically pytest, i.e. model-written test and conftest code) in the worktree before the shadow-ledger diff and the merge gate. Proof (pv.py): a model-written script run as a verifier AC rewrote the shared `.git/config` to add `core.sshCommand`; the verifier reported PASS and the fingerprint changed with nothing re-checking (only two git_exec_surface call sites exist: claim and worker exit). The verifier env is `{}` so no secrets there, but the planted config then reaches the merge gate and F1.

F5. MEDIUM. (e) is bypassed by ambient env. The refusal keys on `args.invoker == "sdk-tools"`; the real switch is `CARDS_RUNNER_USE_TOOLS`, which the spawner forwards from the daemon env for any `--invoker sdk` run. Proof (pd.py): `--invoker sdk --pr-gate` with `CARDS_RUNNER_USE_TOOLS=1` in env returns rc 0 and builds the daemon with `pr_gate_enabled=True` while workers run in tool mode. A stale export or a `.env` entry silently defeats the guard.

F6. MEDIUM (test gap, not a runtime bug). Mutant M10 (claim-time baseline never recorded, `git_surface=None` always, which disables the whole c2 check in production) SURVIVES the full 817-test suite. The only daemon test injects a hand-built `_WorkerHandle(git_surface="before")` and mocks `git_exec_surface`; nothing exercises `_prepare_and_spawn` wiring. c2 can be dead and CI stays green.

F7. LOW. Fingerprint false positive: `git branch --track` / `checkout -b X --track` writes `branch.X.*` to the shared config, so a legitimate tool-mode run blocks the card (git_surface_changed). Plain add, commit, checkout -b do not change it.

F8. LOW. Race on the empty-hooks dir: `%TEMP%\cards-runner-nohooks-*` is in a model-writable temp dir (TEMP is kept by the scrub) and is reused across calls; a concurrently running executor could drop a `pre-push` there between the emptiness check and the push. Narrow window, needs max_parallel > 1.

F9. LOW / residual (declared out of scope, noted). gh config under %APPDATA%\GitHub CLI is model-writable and not fingerprinted (dormant while the PR gate stays off). PATH starts with `C:\Users\Drama\bin` on this host, a user-owned dir a same-user shell can write a `git.exe` into (I did not plant anything there). Same-user process memory and tool-shell network egress: not tested, as instructed.

F10. INFO. M16 (read-modifier set widened with `-f`, `--file`, `--global`) survives: no test refuses `config --get --global ...` or `-f file --get`. These are reads only, so low impact, but the refusal tests only cover write forms.

## Mutant results (24 of mine, run against the touched-area test files; M10 re-run on the full suite)
KILLED: M01 (pop key to get), M02 (belt dict(os.environ)), M03 (always inject key), M04 (tools mode loses key), M05 (unloadable map gets key), M06 (gh runner back to os.environ.copy), M07 (drop hooksPath override), M08 (drop --no-textconv), M09 (diff_stats env=None), M11 (surface-changed always False), M12 (check only on non-clean exit), M13 (hooks not fingerprinted), M14 (shared config not fingerprinted), M15 (config read-only gate off), M17 (pr-gate refusal removed), M18 (drop --no-ext-diff), M20 (passthrough set widened, #84), M21 (pin missing-file not refused, #84), M22 (spawner forwards any base URL, #84), M23 (pin check swallowed, #84).
SURVIVED: M10 (no claim-time baseline: real gap, F6), M16 (F10), M19 (#84 userinfo check removed: equivalent mutant, the host:port regexp also rejects `@`), M24 (daemon gh/git calls lose GH credentials: functional regression with no test).
#84 properties (pin fail-closed, base-URL allowlist, exact passthrough set): no regression, all three guarded by killing tests.

## Theater check

| Author claimed | Verification found | Verdict |
|---|---|---|
| Worker pops key; belt uses fresh scrub | Reproduced with real tools and decoys, no leak by env or command line | CONFIRMED |
| Key only forwarded when usable | Reproduced | CONFIRMED |
| Hardened env stops planted code in the daemon's git/gh in a card worktree | Holds for hooks, core.hooksPath, fsmonitor, textconv, ext-diff. Does not hold for sshCommand, receivepack, credential helpers, pushurl, includes (c1 is a denylist) | PARTIAL |
| c2 blocks the card BEFORE verifier, gate, ledger touch git | Order in `_post_worker_exit` is right for the tampering card. But the daemon's worktree git (F1) is not guarded, baseline is re-taken from a tampered repo (F3), verifier code can change config after the check (F4), global config is outside the fingerprint (F2) | PARTIAL / THEATER for the "daemon runs no model code" goal |
| sdk-tools + pr-gate refused permanently | Only by CLI flag value; USE_TOOLS env bypasses (F5) | PARTIAL |
| 11 author mutants red | Not independently listed, but my equivalents for each claim are red except M10 | CONFIRMED with the F6 exception |
| 817 pass, ruff clean | Reproduced | CONFIRMED |

## Blockers (must fix before claiming the threat model is met)
1. F1: run worktree.py git through `hardened_git_env(keep_gh_credentials=False)` (or at minimum a scrubbed env), and repair or quarantine on tamper (stop claiming, or restore a known-good config and hooks from a snapshot taken once at daemon boot, not per claim).
2. F2: set `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` to an empty file (and drop XDG_CONFIG_HOME/HOME influence) in `hardened_git_env`, and either fingerprint the real global config or keep HOME away from the tool shell.
3. F3/F4: baseline from a daemon-owned snapshot, and re-check the fingerprint immediately before every daemon git or gh call (merge gate, ledger diff), not only at worker exit.
4. F5: refuse on `CARDS_RUNNER_USE_TOOLS` being truthy (not just on the CLI value), or pop it for non-tools invokers.
5. F6: add a test that drives `_prepare_and_spawn` and asserts a non-None baseline reaches the handle, and a sdk-tools test through `_post_worker_exit` with a real tampered repo.

## Warnings
- c1 should invert from denylist to allowlist: pin `credential.helper`, `core.sshCommand`, `remote.*.receivepack/uploadpack/pushurl/proxy`, `url.*.insteadOf`, `include`, `includeIf` as well, or run with `GIT_CONFIG_NOSYSTEM`, empty GLOBAL, and `-c` clearing of those keys.
- The doc claim "protects the HUMAN too" is not true for global config or for a plant that fires between baselines.

## Reproduce
probes\pa_worker.py + run_pa.ps1 (claim a), pc_c1.py / pc_c1b.py (matrix), pg1_worktree.py (F1/F3), pd.py (d, F5, git.exe, redirect, F7, global write), pv.py (F4), mutants.py / mutants_full.py (mutants), all in scratchpad\verify-ac85\probes. Run with scratchpad\verify-ac85\venv\Scripts\python.exe (wrap.py adds a faulthandler timeout).
