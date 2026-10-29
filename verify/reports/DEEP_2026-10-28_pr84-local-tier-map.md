# Deep-verify report -- agilecards #84 (local tier map, no key) -- 2026-10-28

Verified-Commit: e4461bcb7c5bbd3813e478c3a8f135ac2c8e2208
Branch: fix/local-map-no-key
PR: #84
Operator: Daedalus session (orchestrator), four independent verifier subagents; author was the PromptBuilder session
Environment: Windows 11 desktop (DrewsPC), fresh clones + venvs under the Daedalus scratchpad

## Rounds

| round | sha | verdict | carried to next round |
|---|---|---|---|
| r1 | 4de373c | PASS WITH WARNINGS | W1 creds in base URL, W2 relative map path (check vs worker cwd), W3 2-tier naming test, W4 non-local providers false green |
| r2 | d13f402 | PASS WITH WARNINGS | X1 echo in refusal, X2 name-blacklist URL check, X3 fallback after start, X4 passthrough pin, X5 spawn_worker relative path |
| r3 | 27a5c69 | PASS WITH WARNINGS | 4 surviving mutants (test gaps), KeyError at start, vanished map at merge gate |
| r4 | e4461bc | PASS | diff-scoped (27a5c69..e4461bc) plus full suite: 780 passed, ruff clean, all r3 survivors red, 4 new mutants red |

Accepted limitation (in the PR body): a map swapped to a Claude map after start does not re-run the key check; it fails closed only because no key is injected when none was set at start.

## Round 4 report (verbatim)

# Deep Verify round 4: Ginkobaloba/paradigm-agilecards PR #84 (delta 27a5c69..e4461bc)

Tested sha: e4461bcb7c5bbd3813e478c3a8f135ac2c8e2208. gh pr view headRefOid matched; 27a5c69 is an ancestor (merge-base --is-ancestor rc 0, one commit on top, no rewrite). Fresh clone: scratchpad\verify-ac84\r4, fresh venv. No pushes, no secrets read.

Verdict: PASS (one info-level note).

1. Suite: 780 passed in 68s; ruff All checks passed. Matches claim.
2. Four r3 survivors, re-applied by me, each RED (harness scratchpad\verify-ac84\mut5.py, source restored, git status clean):
   - drop bare-'#' refusal: RED (test_anything_beyond_host_port_path_is_refused)
   - spawner log.error echoes URL: RED (caplog assertion)
   - pass CARDS_RUNNER_OPENAI_API_KEY through: RED (exact-set test)
   - openai added to LOCAL_BASE_URL_ENV: RED (valid-URL decoy now forwarded, set test fails)
   Extra mutants also RED: tier-model check dropped, CLI skips _tier_map_problem, CLI drops abs write-back of pinned path, MergeGate catch removed.
3. (a) cli._tier_map_problem, real _cmd_start probes (scratchpad\verify-ac84\p4\probe.py), both sdk and sdk-tools, no key: broken YAML -> rc 2 naming CARDS_RUNNER_TIER_MAP_YAML; tier with missing / empty / null / int / list `model` -> rc 2 "tier N in <file> has no `model`"; tier entry that is a string or null -> rc 2 via loader "no parseable entries". Zero tracebacks in any case. Good map: sdk starts (rc 0); sdk-tools refuses for lack of ANTHROPIC_API_KEY, as intended. Pin/absolutize preserved after moving the load out of _pin_worker_env: relative "good.yaml" from cwd A became absolute in env, and load_tier_map() from a different cwd resolved the same file; dropping the write-back goes RED.
4. (b) MergeGate.apply on CanonicalConfigMissing: returns to_status blocked, merge_status blocked, decision human_review, reason "tier map unavailable, cannot gate", gh.calls == [] on both pr_gate on and off (parametrized). Daemon test drives Daemon._post_worker_exit with a real claim and ends with card.status blocked, merge_status blocked, zero gh calls. Mutant (catch retargeted) turns all 3 tests RED. Catch is narrow: only CanonicalConfigMissing (a RuntimeError subclass raised only from load_tier_map strict paths), only around decide_gate; other exceptions still propagate to _tick. Closes r3 W2 (card no longer stuck/retried silently).
5. (c) .env.example and registry comment say what the ledger claims (endpoints only, path allowed so never put a secret there, set map path is a pin, relative made absolute). Only placeholders (empty values, 127.0.0.1:11434, a commented C:\dev\... example path). No secrets or real hostnames.
6. Hygiene: 7 files, +148/-11: ledger entry, .env.example, cli/__main__.py, merge_gate.py, registry.py (comment only), two test files. Nothing out of scope, no new env passthrough, no em dashes added.

Notes (info, not blocking):
- N1: the blocked reason and the broken-YAML refusal embed the map file path and the YAML parser excerpt (local path and a snippet of the map file). Map files hold no secrets by convention; the merge-gate reason is stored in card payload.
- N2: r3 W1 residual (map swapped to Claude map after start is not re-keyed; fails closed because no key injected) remains known and documented in the ledger. r3 W3 (secret in URL path undetected) is now documented in .env.example and registry.

Overall: PASS
