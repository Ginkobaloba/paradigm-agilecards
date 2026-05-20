# cards_runner.store

The card storage substrate. This package is the chunk 2a deliverable:
it moves the card store from markdown-files-in-subfolders to a
database, behind one swappable interface, per
`docs/design/storage_substrate_v2.md` (Model B, database-canonical
with the card file preserved as a per-run projection).

Chunk 2a is deliberately **additive**. It ships the storage layer as
a standalone, tested package. It does not rewire the runner daemon.
The canonical cutover (the daemon's claim path moving off the
filesystem, the atomic-rename sentinel and the in-place YAML rewriter
being deleted) lands in chunk 2b alongside the real executor, so this
chunk cannot regress the working chunk 1 daemon.

## The repository interface

`repository.CardRepository` is the seam. Everything else in the
runner is meant to depend on this abstract class and nothing more, so
swapping one store for another is a constructor change.

The operations:

- Cards: `create_card`, `get_card`, `query_cards`, `count_cards`,
  `update_card_fields`, `transition`.
- The claim: `claim_card`, a transactional backlog-to-active claim
  that returns `None` when the card was not claimable (already
  claimed, gone, or lost to a concurrent claimer).
- Events: `append_event`, `list_events`. The `card_events` table is
  append-only and is populated from day one.
- Batches: `create_batch`, `get_batch`, `next_batch_id` (a real
  monotonic counter, replacing the v1 `_batches/.counter` file).
- Dependencies: `add_dependency`, `get_dependencies`,
  `get_dependents` (the `depends_on` graph as queryable edge rows).

`_SqlCardRepository` is a shared base that implements everything
except the connection plumbing and the claim. Both concrete stores
extend it, so the only genuinely store-specific code is the claim
primitive, which is exactly where the design pass said the two
stores honestly differ.

## The two implementations

### Dolt (default) — `dolt_store.DoltRepository`

A SQL database with git-style commit, branch, diff, and merge. Cards
are rows, frontmatter scalars are columns, the body is a text column.
Every write becomes a Dolt commit, so the store carries real version
history behind the `card_events` audit table.

The claim is a conditional `UPDATE ... WHERE status = 'backlog'`
inside a Dolt SQL transaction. A Dolt transaction is itself a
short-lived branch that merges into the branch HEAD on `COMMIT`, so
this is the brief's "update on a branch then merge" expressed as the
native Dolt primitive. Two racing claimers both run the guarded
update in their own transaction; the first to commit wins, and the
second is rejected with a serialization failure when Dolt merges its
transaction against the now-changed row. The loser sees `None` and
re-plans.

`DoltServer` manages a `dolt sql-server` process; `DoltRepository` is
a MySQL-protocol client (PyMySQL). They are separate so a fleet of
runners can share one server. `DoltRepository.embedded(dir)` is the
single-instance convenience: it starts a private server and the
repository owns it.

Dolt is not a Python dependency. It needs the `dolt` binary on the
host (`winget install DoltHub.Dolt` on Windows; see dolthub.com for
Linux and macOS) and the `cards-runner[dolt]` extra for PyMySQL.

### SQLite (fallback) — `sqlite_store.SqliteRepository`

The de-risking fallback and the minimal-deploy option: stdlib only,
no server, no install, no network, a single portable file. For a
solo user it is strictly better than the v1 filesystem substrate,
adding real transactions and real queries while staying one file.

The claim is a guarded conditional `UPDATE`. SQLite serializes
writers, so of two claimers exactly one sees an affected-row count of
1 and the other sees 0. The busy timeout makes the loser wait for the
winner's commit rather than raising `database is locked`.

### Choosing one

SQLite covers the solo user and a single-host team. Its hard ceiling
is that it must not live on a network filesystem and allows one
writer at a time, so multiple runners on multiple machines cannot
share one SQLite file. Dolt is the default because it carries the
git-style history the project wants and has a clean concurrency story
for the eventual multi-runner case. Both are behind the same
interface; the choice is a constructor call.

## Schema

Defined per dialect in `schema.py`, identical in shape:

- `cards` — hot fields as typed columns, the long tail of the roughly
  40-field frontmatter as a JSON column (`frontmatter_extra`), the
  verbatim frontmatter text and body as capture columns
  (`frontmatter_raw`, `body_md`). `tenant_id` is the first primary
  key column, present from the first migration, defaulting to
  `default` for solo deployments (storage_substrate_v2.md section
  6.3).
- `card_events` — append-only, one row per lifecycle transition, each
  carrying `actor_id`, `actor_type`, a per-card `seq`, and a JSON
  payload. This is the per-actor audit trail the filesystem substrate
  could not provide, and the proto event log that keeps full event
  sourcing a refactor away rather than a rewrite away.
- `batches` — replaces `_batches/.counter` and the manifest files.
- `dependencies` — explicit `card_id -> depends_on_id` edges.
- `counters` — the monotonic batch-id sequence.

## Projection and migration

`projection.py` converts between a card `.md` file and a `CardRecord`.
`render_card_text(record, verbatim=True)` reassembles the verbatim
capture columns and is byte-faithful to the source file. That is the
guarantee the migration's losslessness rests on.

`migrate_v1.py` is the one-shot importer. It walks a v1 TODO tree,
parses every card, writes rows and `card_events`, then proves the
migration was lossless rather than asserting it: every imported card
is projected straight back to Markdown and byte-diffed against the
file it came from. Run it with `cards-runner-migrate --todo-root
C:\dev\todo --store dolt:C:\dev\todo-store` (or `--store
sqlite:cards.db`).

The per-run card file survives. Chunk 2b will project a card into the
executor's worktree exactly as v1 wrote it, so the executor's
ORM-free Markdown interface is preserved completely.

## The PostgreSQL path (documented, not built)

PostgreSQL is the multi-host, multi-tenant answer for when the
distributed-fleet or SaaS ambition activates. It is documented here,
not built, because building it before a tenant needs it is premature
(storage_substrate_v2.md section 4.5, decision 1). The point of the
repository interface is that the day Postgres is needed is a new
implementation and a connection string, not a rewrite.

A `PostgresRepository(_SqlCardRepository)` would:

- Reuse `_SqlCardRepository` unchanged. The generic DML is already
  dialect-portable; `schema.py` gains a third DDL variant (`SERIAL`
  or `IDENTITY`, `JSONB` columns, `TEXT` for the capture fields).
- Implement the claim with `SELECT ... FOR UPDATE SKIP LOCKED`, the
  textbook primitive for several runners pulling distinct cards off a
  backlog with zero lock contention:

  ```sql
  SELECT card_id FROM cards
   WHERE tenant_id = %s AND status = 'backlog'
   ORDER BY created
   FOR UPDATE SKIP LOCKED
   LIMIT 1;
  ```

  The selected row is then updated to `active` inside the same
  transaction. `SKIP LOCKED` means each runner gets a different card
  or no card, never the same card as another runner.
- Carry multi-tenancy properly: row-level security policies keyed on
  `tenant_id` so a query that forgets its tenant filter still cannot
  read another tenant's rows. The `tenant_id` column already in the
  schema is what makes this a policy addition rather than a
  migration.

What Postgres does not get is offline, zero-ops operation, which is
why it is not the default and why SQLite stays the minimal-deploy
option. One canonical model (Model B), one claim contract, three
deployments.

## Tests

The suite lives in `runner/tests/store/`. It runs against both stores
via a parametrized fixture; the Dolt half is skipped (not failed)
when the `dolt` binary is not on the host.

```
cd runner
python -m pytest tests/store -q
```

Coverage: the repository contract against both stores, claim
concurrency under racing threads, migration integrity against a
synthetic v1 corpus, projection round-trip byte fidelity, and
`card_events` behavior. See the chunk 2a handoff for the documented
test gaps.
