# cards-runner

The harness that drives agile-cards work cards to terminal state: it
claims cards from a canonical card store, spawns per-card workers
under a Windows Job Object, mirrors heartbeats, reclaims orphans, and
lands worker results back into the store.

This is a **4-chunk build** per the runner design and
`docs/design/storage_substrate_v2.md`.

- **Chunk 1** shipped the thin daemon, the filesystem claim path, the
  stub executor, orphan reclaim, and the worktree isolation plumbing.
- **Chunk 2a** shipped the storage layer: a `CardRepository`
  interface with SQLite and Dolt stores, a `card_events` audit table,
  and a v1 filesystem-to-database migration tool.
- **Chunk 2b-i** was the **canonical cutover**. The database is now
  the source of truth. The claim is a transactional store `UPDATE`,
  not an atomic file move; folder-as-state is a `status` column; the
  atomic-rename sentinel and the in-place YAML rewriter are deleted.
- **Chunk 2b-ii** swapped the stub for the real `SdkInvoker` -- an
  Anthropic-SDK-in-process executor with cost-cap hooks, the
  `_winapi.CreateProcess` suspended-spawn Job Object refinement, and
  the confidence cascade. The daemon routes the worker's exit code
  (cost-cap / cascade-exhausted halts go to `blocked`).
- **Chunk 3** (this state) ships the **verifier and the executor tool
  belt**. A two-path cold-read verifier (deterministic handlers plus
  a cascading subjective evaluator) gates the `done` transition: PASS
  -> `done`, FAIL -> `backlog` with `verifier_notes`, NEEDS_STANDUP
  -> `awaiting_standup_review`, internal crash -> `blocked` after two
  retries. The `SdkInvoker` grows a sandboxed file/shell/git tool
  belt (rooted at the per-card worktree, push/pull/fetch refused)
  driven by the SDK's tool-use loop. Run it with `cards-runner start
  --invoker sdk-tools`.
- **Chunk 4** wires merge orchestration (tier-aware gates, the PR
  lifecycle, sibling-review for tiers 3-4, human approval for 5-6)
  and the forensic-worktree reaper.

## How card state works after the cutover

A card's authoritative state is a row in the card store (SQLite by
default, Dolt optional -- one `CardRepository` interface, two
implementations). The daemon:

1. Queries the store for `backlog` cards and claims one with a
   transactional conditional `UPDATE` -- correct under concurrency on
   one host, and across hosts against one shared store.
2. **Projects** the claimed card into a per-run `card.md` file under
   `_runs/<attempt>/`. The worker reads and writes that file exactly
   as a v1 worker read a card in `active/`; it never learns the
   database exists.
3. Spawns the worker under a Job Object with a scrubbed env block.
4. Mirrors the live worker's liveness into the store's
   `last_heartbeat` each poll tick; orphan reclaim reads that column.
5. On worker exit, parses the projected file back and lands the
   executor-owned deltas (body, `finished_at`, `actual_tokens`, ...)
   into the store with an `executed` event.

The runner holds no durable card state -- the store is the single
source of truth, and a crashed daemon reconstructs everything from it.

## Quickstart

```powershell
cd C:\dev\agile-cards\runner
pip install -e .[dev]

# Boot the daemon. The store defaults to sqlite:<todo-root>\cards.db.
cards-runner start --todo-root C:\dev\todo

# In another shell:
cards-runner status
cards-runner reclaim b001-03-add-rate-limit-middleware
cards-runner stop

# Migrate an existing v1 filesystem card tree into a store first:
cards-runner-migrate --todo-root C:\dev\todo --store sqlite:C:\dev\todo\cards.db
```

The `--store` flag (or `CARDS_STORE` env var) overrides the default,
e.g. `--store dolt:C:\dev\todo-store`.

## Layout

```
runner/
  pyproject.toml
  src/cards_runner/
    cli/              command surface (start, stop, status, reclaim)
    common/           card I/O, atomic ops, env scrub, locks, Job Object
    daemon/           polling loop, store-backed claim, worktree,
                      orphan reclaim, verifier dispatch (chunk 3)
    store/            CardRepository interface + SQLite/Dolt stores
    verifier/         cold-read verifier (chunk 3): canonical AC
                      types, deterministic handlers, cascading
                      subjective evaluator, orchestrator
    worker_stub/      stub + SDK executors, cost governor, tool belt
                      (chunk 3 file/shell/git tools), Invoker seam
  tests/              pytest suite
```

## Architectural decisions baked in

From the multi-agent paradigm-shift reviews and the storage design:

1. **Process model.** Thin long-running daemon plus per-card worker
   subprocesses. State in the store; the daemon is stateless across
   restarts.
2. **Database-canonical (Model B).** The store is the source of
   truth; the card file is a per-run projection. The executor keeps
   its ORM-free Markdown interface. SQLite is the zero-ops default,
   Dolt the opt-in for the multi-runner case.
3. **Executor invocation.** The per-card worker imports the Anthropic
   SDK in-process (chunk 2b-ii). The `Invoker` seam keeps the daemon
   ignorant of stub-vs-real. Each worker is wrapped in a Job Object
   so the daemon can hard-kill the whole process tree.
4. **Cost-cap enforcement.** SDK hooks for sub-second budget
   enforcement, Job Object resource limits as the OS backstop,
   wall-clock `TerminateProcess` as the last resort (chunk 2b-ii).
