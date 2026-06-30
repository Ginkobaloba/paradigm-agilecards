# Handoff: runner chunk 1

Date: 2026-05-19
Branch: `feature/runner-chunk-1`
Author: orchestrator pass (Drew available for ratify)

## What shipped

A working agile-cards runner daemon plus a stub executor under
`agile-cards/runner/`. Ships the chunk 1 slice per
`docs/design/runner_v1_design.md` section 12. The daemon is wired
end-to-end against a synthetic 3-card backlog (the integration test
proves it). Zero LLM tokens consumed at any point in chunk 1.

Layout:

```
runner/
  pyproject.toml
  README.md
  src/cards_runner/
    __init__.py
    cli/__main__.py            cards-runner start | stop | status | reclaim
    common/
      atomic.py                os.replace plus tempfile-rename write
      card_io.py               YAML frontmatter read / targeted in-place rewrite
      env_scrub.py             allowlist-only env block for workers
      locks.py                 FileLock (msvcrt / fcntl), pid_alive, mutex helper
      logging_setup.py         daemon + worker logging
      process_group.py         Windows Job Object spawn wrapper plus POSIX path
      types.py                 RuntimePaths, DaemonConfig, CardSnapshot, ...
    daemon/
      atomic_rename_sentinel.py embedded host check, sentinel stamp
      claim.py                  atomic backlog -> active with frontmatter stamp
      daemon.py                 singleton, polling loop, reap, drain
      orphan.py                 stale-heartbeat scan and reclaim
      spawner.py                worker spawn (scrubbed env, Job Object, logs)
      worktree.py               git worktree add via PowerShell, post-create verify
    worker_stub/
      invoker.py                Invoker protocol + StubInvoker (no LLM)
      worker.py                 lifecycle: heartbeat thread, invoke, stamp
  tests/                        25 tests, all passing on Windows
```

Verified by `pytest tests/`:

- Atomic claim under concurrency: 8 worker subprocesses race on
  one card, exactly one wins (process-level race, which is what
  production looks like).
- Worktree creation in skip-git mode and the real-git failure
  surface.
- Stub worker heartbeat propagates to both the heartbeat file and
  the card frontmatter mid-run.
- Orphan reclaim triggers when `last_heartbeat` ages past
  `orphan_timeout_minutes`; the card returns to backlog/ with claim
  fields cleared.
- Daemon restart on a clean active card leaves it alone; on a stale
  active card reclaims; on a malformed-claim active card re-stamps.
- Env scrubbing drops `ANTHROPIC_*`, `OPENAI_*`, `AWS_*`, `GH_TOKEN`,
  `_NT_SYMBOL_PATH` (and the prefix families) before any worker
  spawn. Subprocess-level dump asserts the leak surface is clean.
- Atomic-rename-test sentinel: missing sentinel + embedded test
  pass -> stamp and allow `max_parallel`. Missing sentinel +
  embedded test fail -> force `max_parallel: 1`.
- Three-card synthetic integration: backlog drains, every card
  gets a `## Completion notes` block, daemon stops cleanly.

## Architectural decisions baked in

These came from the multi-agent paradigm-shift exercise on the
three load-bearing forks. Chunk 1 honors all three; the seams that
chunk 2 needs are already in place.

**Fork 1 (process model): KEEP DESIGN.** Thin long-running daemon
plus per-card worker subprocesses. State on disk. The daemon holds
no durable in-memory state across restarts. Crash-tolerant by
construction.

**Fork 2 (executor invocation): OVERRIDE DESIGN.** Use the
Anthropic SDK in-process per worker subprocess in chunk 2.
`worker_stub/invoker.py` is the seam: chunk 1 ships `StubInvoker`,
chunk 2 ships `SdkInvoker`. The daemon and worker runner do not
know which is in use. Each worker is wrapped in a Windows Job
Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` plus
`JOB_OBJECT_LIMIT_BREAKAWAY_OK`, so closing the job handle kills
the entire process tree. `taskkill /T` is not reliable enough.

Known limitation in the chunk 1 Job Object wiring: production
should create suspended, assign to job, then resume. Python's
`subprocess.Popen` does not expose the main-thread handle (only
the process handle), so calling `ResumeThread(process_handle)` is
a no-op. Chunk 1 ships the assign-immediately-after-CreateProcess
variant. The tree-kill semantics still work (the job is in place
before the child does anything interesting); the only window
exposed is a few microseconds where a descendant spawned at
CreateProcess time could escape the job. Stub workers have no
descendants. Chunk 2's followup: drop to `_winapi.CreateProcess`
directly so we own the main thread handle.

**Fork 3 (cost cap enforcement): OVERRIDE DESIGN.** Chunk 2 uses
Anthropic SDK **hooks** (pre-tool-use and pre-message) for
sub-second budget enforcement. Job Object resource limits are the
OS-level backstop. Wall-clock `TerminateProcess` at
`force_kill_after_seconds` (default 90s) is the last-resort safety
net. The sentinel-file halt drops to a FALLBACK mechanism only.
Chunk 1 ships the Job Object spawn and the wall-clock fallback;
the SDK hooks land with the SDK in chunk 2.

## What is intentionally NOT in chunk 1

- Real LLM execution. The stub Invoker sleeps and returns a fake
  completion. Token count is zero.
- Cost cap of any flavor. No SDK is loaded, so there is nothing to
  meter.
- Verifier dispatch. Cards stay in active/ after the stub worker
  exits; no transition to done/. Chunks 3 wires the verifier.
- Merge orchestration, sibling review, PR creation. Chunk 3 and 4.
- Structured event stream (`events.jsonl`). Chunk 4.
- AC amendment surfacing. Chunk 4.
- The reaper. Worktrees accumulate under `_runs/`. Chunk 4 lands
  the reaper that honors the 24h forensic TTL.
- The full CLI surface (`verify`, `approve`, `pause`, `resume`,
  `doctor`, `pricing reload`). Chunk 1 ships only `start`, `stop`,
  `status`, `reclaim` per the chunk 1 ask.

## Notes for the next engineer

- On Drew's Windows machine the embedded atomic-rename test
  occasionally returns False (NTFS plus AV plus indexer can let
  two concurrent renames "succeed" in the same round). This is
  exactly what the sentinel exists to catch; the daemon correctly
  demotes to `max_parallel: 1` in that case. Re-run
  `agile-cards/tests/atomic_rename_test.ps1` outside the Temp
  directory (which is heavily filtered) to confirm whether the
  underlying volume is the issue or just the Temp scratch area.
- The Linux FUSE mount under `/sessions/.../mnt/agile-cards/` has
  stale-cache issues against in-flight Windows edits; rely on
  PowerShell for git operations and for running pytest against
  this branch.
- The card_io rewriter only touches a fixed allowlist of scalar
  frontmatter fields (status, claimed_by, started_at,
  last_heartbeat, finished_at, attempt_trace_id, model_used). It
  leaves planner-owned fields byte-identical, which keeps diffs
  surgical. Chunk 2 needs to extend this so the worker can write
  `actual_tokens` (currently a no-op for unset fields).

## How to run

```powershell
cd C:\dev\agile-cards\runner
$env:PYTHONPATH = "src"
python -m pytest tests/ -v
```

Or:

```powershell
pip install -e .[dev]
cards-runner start --todo-root C:\dev\todo --skip-worktree
# In another shell:
cards-runner status
cards-runner reclaim bTST-01-test --force
cards-runner stop
```

End of handoff.
