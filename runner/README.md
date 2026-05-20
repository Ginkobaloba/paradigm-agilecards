# cards-runner

The harness that polls `C:\dev\todo\backlog\`, claims cards atomically into
`active/`, spawns per-card workers under a Windows Job Object, and drives
cards to terminal state.

This is **chunk 1 of 4** per `docs/design/runner_v1_design.md`. Chunk 1 ships:

- The thin long-running daemon (polling, claim arbitration, parallelism caps).
- Worktree creation under a global mutex with the six isolation requirements.
- A **stub executor** that simulates worker lifecycle without making any LLM
  calls. Total token cost of chunk 1 is zero.
- Orphan reclaim driven by `last_heartbeat`.
- The atomic-rename-test sentinel gate (parallel mode disabled until the
  test passes on the host machine).
- CLI: `start`, `stop`, `status`, `reclaim`.

Chunk 2 swaps the stub Invoker for the real Anthropic SDK in-process per
worker, wires SDK hooks for cost-cap enforcement, and lights up the
two-layer cost cap. The seams are already in place.

## Architectural decisions baked in

These come from the multi-agent paradigm-shift review on the three
load-bearing forks (process model, executor invocation, cost cap):

1. **Process model.** Thin long-running daemon plus per-card worker
   subprocesses. State on disk. The daemon is stateless across restarts.
2. **Executor invocation.** Per-card worker imports the Anthropic SDK
   in-process (NOT a `claude` CLI shell-out). The `Invoker` seam keeps the
   abstraction so a future ensemble-executor can plug in. Chunk 1 uses a
   stub `Invoker` that does no LLM work. Each worker is wrapped in a
   Windows **Job Object** with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so the
   daemon can hard-kill the entire process tree if needed.
3. **Cost cap enforcement.** Chunk 2 uses Anthropic SDK **hooks**
   (pre-tool-use, pre-message) for sub-second budget enforcement. Job
   Object resource limits act as the OS-level backstop. Wall-clock
   `TerminateProcess` is the last-resort safety net. The sentinel-file
   halt remains as a FALLBACK path only.

## Quickstart

```powershell
cd C:\dev\agile-cards\runner
pip install -e .[dev]

# Boot the daemon in the foreground against the default TODO root.
cards-runner start --todo-root C:\dev\todo

# In another shell:
cards-runner status
cards-runner reclaim b001-03-add-rate-limit-middleware
cards-runner stop
```

## Layout

```
runner/
  pyproject.toml
  src/cards_runner/
    cli/              command surface
    common/           card I/O, atomic ops, env scrub, locks, Job Object
    daemon/           polling, claim, worktree creation, orphan reclaim
    worker_stub/      stub executor + Invoker seam
  tests/              pytest suite (concurrent claim, orphan, env scrub, ...)
```
