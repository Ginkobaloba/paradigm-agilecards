# Handoff: runner chunk 2b-i (the canonical cutover)

Date: 2026-05-20
Branch: `feature/runner-chunk-2b` (off `feature/runner-chunk-2a`)
Author: orchestrator pass (Drew available for ratify)

## Why this is "2b-i" and not "chunk 2b"

The brief for chunk 2b was two coupled things: the canonical cutover
(the daemon's claim moving off the filesystem onto the chunk 2a
store) and the real executor (the stub worker replaced by an
SDK-in-process worker with cost-cap enforcement). The brief
pre-approved splitting if 2b did not fit one work window. It does
not, and the split is at a clean seam.

This is **chunk 2b-i: the canonical cutover only.** Chunk 2b-ii is
the real executor.

The reasoning, stated plainly so it can be checked:

- The cutover is self-contained and fully verifiable at **zero token
  cost** -- it is the chunk 2a storage layer wired in behind a stub
  worker. The brief specifically said the cutover "is the risky part;
  it must be exercised end to end," and that is exactly what fits and
  finishes cleanly in one window.
- The real executor needs the Anthropic Agent SDK, which is **not
  installed on this host**, and genuinely verifying it would **burn
  real API tokens**. Building it blind and shipping it unexercised in
  the same window as the risky cutover would put two unproven things
  in one PR. The `Invoker` seam (`worker_stub/invoker.py`) exists
  precisely so the executor can land later without touching the
  daemon.

This is the same call chunk 2a made when it split from chunk 2, and
for the same reason: ship the testable half cleanly, hand off the
half that needs a tool the environment does not have.

## What shipped

The daemon's claim path is now database-canonical (Model B,
`storage_substrate_v2.md`). The chunk 2a storage layer is no longer a
standalone package -- it **is** the runner's state.

- **The claim is a transactional store `UPDATE`.** `daemon._tick`
  queries `repo.query_cards(status="backlog")` and claims with
  `repo.claim_card(...)`, which returns `None` on a lost race exactly
  as a lost atomic-move did in v1.
- **Folder-as-state is gone.** There is no `backlog/`, `active/`,
  `done/` tree. A card's state is its `status` column. `RuntimePaths`
  lost every subfolder field; `ensure()` now only makes `_runs/`,
  `_signals/`, and the TODO root.
- **The atomic-rename sentinel is deleted** (`atomic_rename_sentinel.py`
  removed), along with the `max_parallel` demotion it drove. The
  transactional claim is correct under concurrency, so there is
  nothing left for the sentinel to gate.
- **The in-place YAML rewriter is deleted.** `card_io._rewrite_scalar_fields`
  and its allowlist are gone; `write_card_file` is now a plain full
  `yaml.safe_dump`. The projected card file is an ephemeral per-run
  view -- nobody diffs it -- so surgical-diff fidelity is no longer
  needed, and the worker can write any field (including the
  previously unwriteable list-typed history fields).
- **The daemon's `claim.py` is deleted.** The claim is one repository
  call; there is no module left to host.
- **Per-run card projection.** On claim the daemon writes the card to
  `_runs/<attempt>/card.md` via `projection.project_card_file`. The
  worker reads and writes that file exactly as a v1 worker read a
  card in `active/`. On worker exit the daemon parses it back and
  lands the deltas with `repo.apply_executor_result`.
- **`apply_executor_result`** is a new repository method (abstract on
  `CardRepository`, concrete and dialect-generic on
  `_SqlCardRepository`, so SQLite and Dolt both inherit it). It is
  the worker-exit write-back: body plus the executor-owned
  frontmatter fields plus one `executed` event. It deliberately does
  **not** change `status`.
- **Orphan reclaim is store-backed.** `scan_for_orphans` queries
  `status="active"` and checks the `last_heartbeat` column; `reclaim`
  is a `transition` back to `backlog` that appends a `reclaimed`
  event and preserves every other field.
- **Heartbeat mirroring.** The worker writes its own heartbeat into
  its projected file (and touches the worktree heartbeat file). The
  daemon mirrors liveness into the store's `last_heartbeat` each tick
  for every card whose worker process the OS still reports alive.
  Orphan reclaim reads that column. A stale heartbeat is therefore
  exactly equivalent to a dead worker.
- **CLI.** `start` gains `--store` (default `sqlite:<todo>/cards.db`).
  `status` and `reclaim` read the store, not a filesystem tree.

```
runner/src/cards_runner/
  store/
    __init__.py       + build_repository(), default_store_spec()
    repository.py     + apply_executor_result()
    migrate_v1.py     _build_repo delegates to build_repository
  common/
    types.py          RuntimePaths trimmed; DaemonConfig.store_spec; ClaimedCard reshaped
    card_io.py         surgical rewriter -> full yaml.safe_dump
    atomic.py          docstring only (atomic_move kept as a generic helper)
  daemon/
    daemon.py          store-backed claim / project / reap / reclaim
    orphan.py          store-backed scan + reclaim
    spawner.py         injects the projected card path
    claim.py                 DELETED
    atomic_rename_sentinel.py DELETED
  worker_stub/
    worker.py          writes the projected file; full-dump-friendly stamping
  cli/__main__.py      --store; store-backed status / reclaim
```

## What is verified

`python -m pytest tests/` -- **62 passed, 29 skipped** (the Dolt
half of the chunk 2a store suite skips, not fails, because the
`dolt` binary is absent). `ruff check` is clean. Every source and
test file `py_compile`s.

- `test_daemon_integration.py` -- a 3-card synthetic backlog runs end
  to end through the store: claimed, projected, a real worker
  subprocess spawned under a Job Object, reaped, results landed. Each
  card ends `active` with completion notes in its stored `body_md`,
  one `claimed` event, and one `executed` event. This is the
  end-to-end exercise of the risky cutover the brief asked for.
- `test_daemon_restart.py` -- boot reconcile leaves a fresh `active`
  card alone, reclaims a stale one, and the transactional claim
  stamps all four claim fields atomically (the v1 malformed-claim
  window cannot exist anymore).
- `test_orphan_reclaim.py` -- store-backed scan flags a card after
  its heartbeat goes stale; `reclaim` returns it to `backlog` with
  the claim fields cleared and a `reclaimed` event appended;
  `force_reclaim` rejects a non-active card and a missing one.
- `test_heartbeat.py` -- the stub worker drives the projected card
  file: completion notes, `finished_at`, `actual_tokens`, an
  advancing heartbeat.
- The chunk 2a store suite (contract, claim concurrency, migration,
  projection) still passes against SQLite -- the `repository.py` and
  `store/__init__.py` changes did not regress it.
- CLI smoke test: `cards-runner status` opens an empty store and
  reports zero counts; `cards-runner reclaim <missing>` exits 3.

## Decisions baked in

**The 2b-i / 2b-ii split.** Stated above. Genuine fork resolved by
the same logic chunk 2a used; documented, not brought back.

**SQLite is the canonical store on this host.** Dolt is not
installed and `winget install DoltHub.Dolt` needs a download the
brief said not to force. SQLite is the designed fallback for exactly
this case (`storage_substrate_v2.md` section 4.1) and is what the
daemon defaults to (`sqlite:<todo_root>/cards.db`). The Dolt store
code is untouched and inherits `apply_executor_result` from the
shared SQL base, but the cutover was **not** exercised against Dolt
on this host -- see gaps.

**The projected card file lives in `_runs/<attempt>/card.md`, not
inside the git worktree.** `storage_substrate_v2.md` loosely says
"into the worktree," but a `.md` file inside the project-repo
worktree would show as untracked in `git status` and contaminate the
executor's clean-state check. The run dir is the contamination-free
home RUNNER_CONTRACT.md already designates for per-card scratch
(`_runs/<trace_id>/`). `CARDS_RUNNER_CARD_PATH` points there;
`cwd` is still the worktree.

**The daemon owns every store write; the worker never touches the
store.** The worker is purely file-based (`storage_substrate_v2.md`
section 4.1: "executors do not write the database directly"). This
keeps the executor's ORM-free Markdown contract intact and means a
fleet of workers needs no store connections.

**The worker-exit write-back takes only specific trusted fields.**
`_post_worker_exit` pulls `body_md` plus `finished_at`,
`last_heartbeat`, `actual_tokens`, `actual_duration_minutes`,
`model_used` from the projected file -- not the whole frontmatter.
Even a worker that scribbled on `acceptance_checks` cannot corrupt
the store through this path. Full AC-edit detection (the amendment
contract) is chunk 3/4.

**A finished stub does not transition the card.** `apply_executor_result`
leaves `status` unchanged; the card stays `active` after a clean
stub run, with an `executed` event as the audit record. Moving a
card to `done` is the verifier's job (chunk 3). This is chunk-1
parity (chunk 1 left completed stubs in `active/`).

**Built and tested directly on the Windows host.** Chunk 2a built in
a Linux `/tmp` mirror and delivered file-by-file because the Linux
FUSE mount is no-unlink and serves stale cache. This pass kept the
entire edit-and-test loop on Windows-native tools (the file tools
plus PowerShell `pytest`), so the stale Linux mount is never in the
delivery path at all -- the protocol's goal, reached more directly.
File integrity was checked with `py_compile` across every file and
byte counts after writes.

**`pymysql` was installed; `mypy` was not run.** `pymysql` (a
declared `dev` dependency) was `pip install --user`'d so the chunk
2a store suite could collect. `mypy` was **not**: `pip install mypy`
resolved to `mypy 2.1.0` pulling non-standard `librt` and
`ast-serialize` packages -- the exact supply-chain red flag chunk
2a's handoff named and declined. It was uninstalled before being
executed. `ruff` (a single Rust binary, low surface) ran clean.

## What is intentionally NOT in chunk 2b-i

- The real executor. `worker_stub` is still the stub. No SDK, no
  tokens. All of chunk 2b-ii.
- Cost-cap enforcement. No SDK, nothing to meter. The `HALT_SENTINEL`
  constant and the `EXIT_COST_CAP_HALT` exit code are reserved.
- The `_winapi.CreateProcess` Job Object refinement. Still the chunk
  1 assign-after-CreateProcess variant. Chunk 2b-ii.
- Verifier dispatch and the `done` transition. Chunk 3.
- Merge orchestration, the reaper, AC-amendment surfacing. Chunks 3-4.
- Dependency gating, story-drift, and the pre-approval gate at claim
  time. `_is_eligible` is a pass-through with a comment; chunks 3-4
  read the `dependencies` table and signal markers.

## Documented gaps

- **`mypy --strict` was not run.** Same gap as chunk 2a, now with a
  named cause: the available `mypy` on PyPI for this environment
  pulls suspect non-standard dependencies. The code is written to
  strict standards and `pyproject.toml` keeps `strict = true`. Run
  mypy from a trusted source on a normal host.
- **The cutover is verified on SQLite only.** Dolt is not installed.
  The Dolt store inherits `apply_executor_result` from the shared
  base and the daemon is store-agnostic, so there is no reason to
  expect a Dolt-specific failure, but it has not been run. Installing
  Dolt and re-running the suite is a 2b-ii / deploy step.
- **No real v1 corpus migrated.** Same as chunk 2a -- `C:\dev\todo`
  has no live cards. `cards-runner-migrate` is unchanged and still
  verified against the synthetic corpus.
- **Daemon-crash-then-fast-restart edge.** With
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, a daemon crash kills its
  workers. A card left `active` with a not-yet-stale heartbeat is
  conservatively left alone by boot reconcile until the orphan
  timeout (default 120 min). This is pre-existing chunk-1 semantics,
  not a regression; a boot-time "is a worker process actually alive"
  check would tighten it (chunk 4).
- **Long-running daemon re-runs completed stubs.** A stub-completed
  card sits in `active`; if the daemon runs past `orphan_timeout` its
  heartbeat goes stale and it is reclaimed and re-run. Pre-existing
  chunk-1 behavior; the verifier transition (chunk 3) ends it.

## Environment notes for the next engineer

- All git operations go through PowerShell. The Linux sandbox mount
  is no-unlink and serves stale cache; this pass avoided it entirely.
- `feature/runner-chunk-2b` is branched off `feature/runner-chunk-2a`,
  which is off `feature/runner-chunk-1`, none merged to `main`. The
  PRs stack; sequence the merges (chunk 1, then 2a / PR #4, then this).
- `pymysql` is installed `--user` on the host. `dolt` and the
  Anthropic SDK are not.
- The orphaned design branch `design/storage-substrate-v2`
  (commit `20d9268`) was pushed to `origin` at the start of this
  pass and is confirmed on GitHub.

## Chunk 2b-ii, in order

1. **Install Dolt** (`winget install DoltHub.Dolt`) if the
   multi-runner story is wanted; re-run `pytest tests/store` so the
   29 currently-skipped Dolt tests run, and smoke the daemon against
   a `dolt:` store spec.
2. **`SdkInvoker`.** Replace `worker_stub` with the real per-card
   worker importing the Anthropic Agent SDK in-process. The `Invoker`
   protocol in `worker_stub/invoker.py` is the drop-in seam; the
   daemon and `run_worker` do not change.
3. **`_winapi.CreateProcess` Job Object refinement.** Create the
   worker suspended, assign to the job, then resume, so no descendant
   can escape the job in the spawn window (chunk 1's known
   limitation, documented in `process_group.py`).
4. **Cost-cap hooks.** SDK pre-tool-use / pre-message hooks for
   sub-second budget enforcement; Job Object resource limits as the
   OS backstop; wall-clock `TerminateProcess` as the last resort.
   `EXIT_COST_CAP_HALT` (11) is reserved.
5. **The executor cascade.** Confidence-probe escalation per
   RUNNER_CONTRACT.md "Cascade-on-confidence routing"; append
   `escalated` events to `card_events`.
6. **Exit-code routing.** `daemon._post_worker_exit` currently leaves
   every exit `active`. 2b-ii should route a cost-cap halt to
   `blocked` and a clean verified finish toward the verifier. The
   `executed` event payload already carries `exit_code`.

## How to run

```powershell
cd C:\dev\agile-cards\runner
pip install -e .[dev]
$env:PYTHONPATH = "src"
python -m pytest tests/ -q          # 62 pass, 29 skip without dolt

# the daemon, against a SQLite store (the default)
cards-runner start --todo-root C:\dev\todo --skip-worktree
cards-runner status
cards-runner reclaim <card-id> --force
cards-runner stop
```

End of handoff.
