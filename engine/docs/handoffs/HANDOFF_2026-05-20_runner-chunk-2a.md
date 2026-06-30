# Handoff: runner chunk 2a (storage layer)

Date: 2026-05-20
Branch: `feature/runner-chunk-2a` (off `feature/runner-chunk-1`)
Author: orchestrator pass (Drew available for ratify)

## Why this is "2a" and not "chunk 2"

Chunk 2 as briefed was two coupled things: the real executor worker
and the storage substrate migration. That is too large for one work
window, and the brief pre-approved splitting it. This is chunk 2a:
the storage layer only. Chunk 2b is the executor plus the canonical
cutover.

One scoping decision worth stating plainly, because it shapes
everything below. **Chunk 2a is purely additive.** It ships the
storage layer as a standalone, tested package under
`runner/src/cards_runner/store/`. It does not touch the daemon, the
claim path, the YAML rewriter, or the atomic-rename sentinel. The
chunk 1 daemon runs exactly as before after this merges.

The reason: the canonical cutover (the daemon's claim becoming a
repository call, the atomic-rename sentinel and the in-place YAML
rewriter being deleted, folder-as-state becoming a status column) is
the risky part. Folding it into 2a would leave the tree half-rewired
and could regress a working daemon. Putting it in 2b, next to the
executor that exercises it end-to-end, is lower risk. So the brief's
"delete the sentinel, delete the rewriter" instructions land in 2b,
not here. They are not forgotten; they are sequenced.

## What shipped

A database-canonical card store behind one repository interface, per
`docs/design/storage_substrate_v2.md` (Model B). Two concrete
implementations, a v1 migration tool, and a test suite.

```
runner/src/cards_runner/store/
  __init__.py        package exports
  models.py          CardRecord, CardEvent, Batch, Dependency, enums
  schema.py          column lists, per-dialect DDL, row mapping
  repository.py      CardRepository interface + shared SQL base class
  projection.py      card .md <-> CardRecord, the lossless round trip
  sqlite_store.py    SQLite implementation (stdlib, zero-ops fallback)
  dolt_store.py      Dolt implementation (default; DoltServer + client)
  migrate_v1.py      one-shot v1 filesystem-to-store migration + verify
  README.md          interface, the two stores, the PostgreSQL path

runner/tests/store/
  conftest.py                      parametrized repo / claim_store fixtures
  store_support.py                 card-text and v1-tree builders
  test_store_projection.py         round-trip byte fidelity (12 tests)
  test_store_contract.py           repository contract, both stores
  test_store_claim_concurrency.py  racing-thread claim, both stores
  test_store_migration.py          migration integrity, both stores
```

`runner/pyproject.toml` gains the `dolt` optional extra (PyMySQL), a
`cards-runner-migrate` script entry point, and a mypy override for
the stubless `pymysql` import.

## What is verified

71 tests pass, run against both stores (the Dolt half skips, not
fails, when the `dolt` binary is absent). `ruff` is clean.

- Repository contract against SQLite and Dolt: create, get, query,
  count, update, transition, claim, events, batches, dependencies.
  The same tests pass against both, which is the proof that the
  concrete store is genuinely swappable.
- Claim concurrency against the real stores: eight threads race one
  card, exactly one wins; six threads race twelve cards, every card
  is claimed exactly once. Each thread is its own repository and its
  own connection, which is what a runner fleet looks like.
- Migration integrity: a synthetic v1 corpus is imported and every
  card is projected straight back to Markdown and byte-diffed against
  its source file. Lossless means that diff is empty across the
  corpus. Card count, subfolder-canonical status, history-to-events,
  and the malformed-card failure path are all covered.
- Projection round-trip byte fidelity, including the awkward cases:
  a blank line after the closing fence, a body that itself contains
  `---`, a missing trailing newline, a missing `id`.

## Decisions baked in

**The claim primitive, per store.** SQLite: a guarded conditional
`UPDATE ... WHERE status = 'backlog'`; the writer serialization makes
exactly one claimer see an affected count of 1. Dolt: a guarded
conditional UPDATE inside a SQL transaction. The brief said "update
on a short-lived branch then merge". A Dolt SQL transaction is
exactly that: it is a short-lived branch that merges into the branch
HEAD on COMMIT. Driving explicit `DOLT_BRANCH` and `DOLT_MERGE` per
claim from many sessions was tried first and fails: the sessions
fight over the shared `main` working set and surface errors that are
not the clean merge conflict the design expected. The transactional
form is the same semantics expressed as the native Dolt primitive.
The first claimer to COMMIT wins; the loser's COMMIT is rejected with
a serialization failure (MySQL error 1213) and the claim returns
None. This was a fork without an obvious answer in the brief's literal
wording; it is resolved here and the reasoning is in `dolt_store.py`.

**Projection and losslessness.** `cards` stores the frontmatter and
body as verbatim capture columns (`frontmatter_raw`, `body_md`)
alongside the typed query columns. `render_card_text(verbatim=True)`
reassembles the captures and is byte-faithful to the source file,
which is what the migration verifier diffs against. Two bugs were
found and fixed while proving this: the frontmatter fence regex used
a greedy `\s*` that ate the blank line after the closing fence, and
the YAML loader resolved bare ISO dates into `datetime` objects that
are not JSON-serializable. Both are fixed in `projection.py` with
comments explaining why.

**`tenant_id` and `card_events` from day one.** Every table is keyed
on `tenant_id` (defaulting to `default`, invisible to a solo user),
because retrofitting a tenant key into a populated schema is among
the most painful changes to defer. `card_events` is append-only and
is written from the first operation, so full event sourcing stays a
refactor away rather than a rewrite away.

**SQLite is a real fallback, not a toy.** It is behind the same
interface, passes the same contract and concurrency tests, and is the
correct minimal-deploy and offline option. Dolt is the default for
the git-style history and the cleaner multi-runner story.

## What is intentionally NOT in chunk 2a

- Any change to the daemon, `claim.py`, `card_io.py`, `orphan.py`, or
  the atomic-rename sentinel. The store is not wired in yet.
- The deletion of the atomic-rename sentinel and the in-place YAML
  rewriter. These happen in 2b's cutover.
- The real executor, `SdkInvoker`, cost-cap hooks, the cascade. All
  of chunk 2b.
- The PostgreSQL implementation. Documented in `store/README.md`,
  not built (storage_substrate_v2.md decision 1: build the interface
  now, the Postgres implementation on first real need).

## Documented test gaps

- **`mypy --strict` was not run.** The code is written to strict
  standards (full annotations, typed dataclasses, no untyped defs)
  and `pyproject.toml` keeps `strict = true`, but mypy could not be
  installed in the build sandbox: the `mypy` package resolved to a
  build that pulls non-standard dependencies (`librt`,
  `ast_serialize`), which was not chased for supply-chain caution.
  Run `mypy --strict` on a normal host (chunk 1's suite already runs
  on the Windows host, which has Python 3.11).
- **No real v1 corpus.** `C:\dev\todo` had the subfolder skeleton but
  zero live cards, so the migration is verified against a synthetic
  corpus. Re-run `cards-runner-migrate` when real cards exist.
- **Dolt is not installed on the Windows host.** It is verified in
  the build sandbox (v2.0.4). Installing it on the host
  (`winget install DoltHub.Dolt`) is a 2b deploy prerequisite.
- **No daemon-plus-store end-to-end test.** By design: the store is
  not wired in until 2b.
- **Body-block decomposition is partial.** The migration turns the
  frontmatter history lists (`cascade_history`,
  `verifier_cascade_history`) into `card_events` rows. Append-only
  blocks inside the card *body* (completion notes, `change_request:`)
  are preserved verbatim in `body_md` but are not decomposed into
  events. Losslessness does not depend on that decomposition; fuller
  body-block extraction is 2b or chunk 4 work.
- The projection assumes `\n` line endings and exact `---` fences.
  The migration verifier byte-diffs every card and would flag a
  non-round-tripping card as a failure rather than silently losing
  data.

## Environment notes for the next engineer

- The repo arrived with a corrupt git index and reflog. The object
  database and branch refs were intact; the index was rebuilt from
  HEAD and the reflog reset. No history was lost.
- The Linux sandbox mount of the workspace is no-unlink (files can be
  created and written but not deleted) and serves stale content after
  Windows-side writes. All git operations and file deletions go
  through PowerShell. The chunk 2a code was built and tested in a
  clean `/tmp` mirror, then delivered to the workspace file by file
  with byte-count parity checks. If you see a stale file from the
  Linux side, trust the Windows side.
- `feature/runner-chunk-2a` is branched off `feature/runner-chunk-1`,
  not `main`, because chunk 1 is not merged to `main`. The chunk 2a
  PR therefore stacks on the chunk 1 PR; sequence the merges.

## How to run

```
cd runner
pip install -e .[dev]            # includes PyMySQL for the Dolt tests
python -m pytest tests/store -q  # Dolt tests skip if `dolt` is absent

# migrate a v1 tree into a store
cards-runner-migrate --todo-root C:\dev\todo --store dolt:C:\dev\todo-store
cards-runner-migrate --todo-root C:\dev\todo --store sqlite:cards.db
```

## Chunk 2b, in order

1. Install Dolt on the host. Stand up the store (run `migrate_v1`
   once if there are real v1 cards).
2. The canonical cutover. Replace `daemon/claim.py`'s `attempt_claim`
   with `repository.claim_card`. Delete `atomic_rename_sentinel.py`
   and the `_rewrite_scalar_fields` path in `card_io.py`. Folder
   scans become `query_cards`. Orphan reclaim reads `last_heartbeat`
   from the store. The runner projects the card file into the
   worktree with `projection.project_card_file` and reads it back on
   worker exit.
3. The real executor: `SdkInvoker`, the in-process Anthropic SDK, the
   `_winapi.CreateProcess` Job Object refinement, cost-cap hooks, the
   executor cascade. This is the rest of the original chunk 2.

End of handoff.
