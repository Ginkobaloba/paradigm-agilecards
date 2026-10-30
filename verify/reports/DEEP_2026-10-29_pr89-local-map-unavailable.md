# Deep-verify report -- PR #89 (--on-local-map-unavailable stop|warn) -- 2026-10-29

Verified-Commit: cd221cb0df03caf3a76ac16f350838a39cb98681
Branch: feat/local-map-warn-or-stop
PR: #89
Operator: independent verifier subagent (Claude), for Daedalus
Environment: Windows 11, Python 3.13.3, ruff 0.15.22 (pinned), fresh clone + venv under verify-ac89\. Fake keys only; no Anthropic calls, no Ollama.

Default value in code: "stop" (DaemonConfig.on_local_map_unavailable and CLI --on-local-map-unavailable default). Not "warn".

## Layers run
| layer | scope | result |
|---|---|---|
| 1-4 | full engine/runner suite 988 passed (301s); ruff check . clean | PASS |
| 5 | n/a (no UI; board display not exercised) | n/a |
| 6 (adversarial) | path probes, merge diff vs both parents, 16 mutants | PASS |

## Evidence
1. Head confirmed cd221cb (gh pr view). Merge parents 85c2060 and b7477cf.
2. Paid-map paths. Only one site selects the hosted map: spawner.plan_worker_map -> MapPlan.fallback, consumed only by daemon._prepare_and_spawn (single spawn_worker call site, per card per attempt, so retry/requeue re-plan). Fallback requires pin set AND pin unloadable AND mode==warn AND key set. Probe (real spawn_worker, FakeLauncher, fake key) over missing, malformed YAML, empty, non-mapping, directory, healthy local, partial:
   - stop: fallback False in all; worker env has no key for missing/malformed/empty/notmap/dir/healthy_local; pin forwarded.
   - warn: missing/malformed/empty/notmap/dir -> fallback True, hosted model named, key present, pin NOT forwarded; healthy_local -> no fallback, no key.
   - partial (a paid tier in a healthy pin): key forwarded in BOTH modes. That is #84 behavior (map is the operator's explicit choice, CLI demands a key at start), not a fallback; no per-card record by design.
   - Ollama down / model tag missing: map still loads, so no fallback; all-local worker has no key and cannot reach Anthropic. sdk-tools maps to cfg.invoker "sdk" so it is planned too. Per-card override: none exists in the spawn path. Env override: only CARDS_RUNNER_TIER_MAP_YAML (pin), covered.
3. Never silent under warn: WARNING log "PAID MODEL", card fields billed_fallback_model + tier_map_fallback_reason, map_fallback event (all asserted by test_warn_names_the_paid_model...), CLI start banner on stderr (UNAVAILABLE or NOTICE). Recording happens before spawn, every attempt.
4. Key under stop / healthy local: not injected (probe + control test + mutant M6 killed). Under warn fallback the worker env gets exactly ANTHROPIC_API_KEY, no pin.
5. Merge correctness: git diff b7477cf..HEAD touches only 8 files, all #89 additions (no #87 deletions): spawner (+MapPlan/plan_worker_map, fallback branch; launcher seam, SandboxLauncher import, CARDS_RUNNER_BASE_SHA all retained), daemon (+plan, +_record_map_fallback; _clone_coordinates retained), types (+field; ClaimedCard base_sha/integration_path/integrated_sha, PATCH_NAME retained). Diff vs 85c2060 shows only #87 content. FakeLauncher edits: test_local_map_fallback changes only the fake (spawn_in_job monkeypatch -> FakeLauncher + launcher=) and assertions untouched; test_local_map_no_key changes only the patch target to cards_runner.common.sandbox.spawn_in_job (real seam, sandbox.py L83/96), assertions untouched.

## Mutants (16; tests: fallback, no_key, secret_isolation, sandbox_launcher, clone_per_worker)
Killed 14: M1 warning log removed; M2 _record_map_fallback call removed; M4 warn acts like stop; M5 stop falls back; M6 key leaked under healthy/stop; M7 spawner ignores map_plan; M8 launcher arg dropped; M9 daemon drops map_plan; M10 fallback keeps pin; M11 warn falls back without key; M12 durable fields omitted; M13 CLI banner removed; M15 planning disabled; M14 BASE_SHA injection removed (killed by test_deep_verify_gaps, outside my initial subset; confirmed).
Survived 2: M3/M3b default flipped stop->warn (types.py and CLI) -- 181 tests still pass. No test pins the default.

## Findings
- F1 (medium, not blocker): _record_map_fallback fails OPEN. If the store write raises, it logs ERROR and the card still spawns on the paid map; the only record is the WARNING log (probe: disk-full repo -> WARNING + ERROR, spawn proceeds). Not silent (log + start banner), but the durable-record half of the property can be skipped. Recommend failing closed (block/defer the card) or at minimum retrying.
- F2 (medium, not blocker): default is unpinned by tests (M3/M3b survive). Default is "stop" today; since defaulting to "warn" is Drew's pending decision, add a test asserting the default so a flip is deliberate. If it becomes "warn", all recording above already exists, so it would not be silent.
- F3 (low): billed_fallback_model names only the starting hosted tier; cascade escalation (up to 2 tiers) can bill a higher hosted model. Start banner lists every paid tier; the stamp does not.
- F4 (low): billed_fallback_model / reason are never cleared; a later retry on a healthy local map leaves a stale stamp (conservative, misleading).
- F5 (info): partial maps get the key in both modes (pre-existing #84), no per-card record.

## Not exercised
Board/frontend display of billed_fallback_model (it lands in frontmatter_extra and projection; no frontend code references it). Real daemon run end to end with a real worker process, real Ollama, real Anthropic (forbidden). Windows job-object real spawn.

## Notes
No blocker confirmed: no silent paid fallback, no paid use under stop, no key leak, no dropped #87 or #89 behavior.

Overall: PASS
