# ADR: Making the FastAPI backend real -- Postgres, CRUD parity, DB-enforced RLS

**Date:** 2026-07-16
**Status:** Accepted (Drew's Decision 1 from the 2026-07-16 alpha audit: build the real
backend instead of shipping alpha on `legacy/board-express/`)
**Context doc:** `docs/audits/AUDIT_2026-07-16_alpha-gap-list.md` (branch
`audit/alpha-gap-list-2026-07-16`), items M1/M2, S2/S3, P1.

---

## Context

The working board runs entirely on `legacy/board-express/` (Express/TS, SQLite,
file-backed cards). The intended replacement, `backend/` (FastAPI), shipped K11's
JWKS auth + org-isolation contract but no CRUD and no persistence -- an in-memory
dict. "Org isolation" is a Python list comprehension, not a database guarantee.
Drew rejected shipping alpha on legacy; this ADR records how the real backend is
built.

## Decisions

### 1. Storage: PostgreSQL 16, SQLAlchemy 2.0 (sync ORM), psycopg 3, Alembic

- **Postgres over SQLite:** row-level security, roles, and concurrent writers are
  hard requirements. SQLite has none of them.
- **Sync SQLAlchemy over async:** the existing routes, tests, and DI pattern are
  sync (`TestClient`, `def` endpoints running in FastAPI's threadpool). Alpha-scale
  traffic does not justify the event-loop complexity tax; the one endpoint that
  needs async (SSE) gets it explicitly. Switching the session layer to asyncpg
  later is contained inside `db.py` if load ever demands it.
- **Alembic from day one:** every schema change, including RLS policies, is a
  versioned migration. No `create_all()` in production code paths.

### 2. Row-level security: enforced by Postgres, not Python

The K11 org filter (`store.py` list comprehension) stays only as a
defense-in-depth layer; the guarantee moves into the database:

- Every tenant table carries `org_id TEXT NOT NULL`.
- `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY`
  (FORCE means even the table owner is subject to policies).
- One policy per table:
  `USING (org_id = current_setting('app.current_org'))` and the equivalent
  `WITH CHECK` for writes, so a forgotten `WHERE org_id = ...` in application
  code returns/affects zero foreign rows instead of leaking.
- **Two database roles:**
  - `agilecards_owner` -- owns the schema, runs migrations. Never used by the app
    at runtime.
  - `agilecards_app` -- what the API connects as. `NOSUPERUSER`, `NOBYPASSRLS`,
    DML grants only (no DDL, no `TRUNCATE`). This is what makes RLS *actually
    apply*; a superuser or owner connection would silently bypass it.
- Per-request: the request's verified `org_id` (from the JWT, never the body) is
  bound with `SET LOCAL app.current_org = :org` inside the request transaction.
  `SET LOCAL` scopes to the transaction, so pooled connections cannot leak an org
  context across requests.
- Policies compare against `NULLIF(current_setting('app.current_org', true), '')`:
  when no org context is bound the comparison is against NULL and matches zero
  rows. A request that never bound an org context fails closed -- it sees an
  empty tenant, never everything.
- Tests prove the guarantee at the DB layer: raw SQL through the `agilecards_app`
  role with org A's context must see zero org-B rows on SELECT/UPDATE/DELETE,
  including deliberately org-unfiltered statements simulating the forgotten-WHERE
  bug.

### 3. Card data model: database-native, legacy shape preserved at the API edge

Legacy stores cards as markdown files with YAML frontmatter and serves
`{file, frontmatter, mtimeMs, body}`. The new model is database-native:

- `cards` table: `id`, `org_id`, `project`, `file` (the legacy stable key,
  kept so existing card identities survive migration), `frontmatter JSONB`,
  `body TEXT`, `updated_at` (serialized as `mtimeMs`), plus extracted hot
  columns the API filters/sorts on.
- The API keeps serving the legacy wire shape (`file`/`frontmatter`/`mtimeMs`/
  `body`) so the frontend contract holds; the shape is an adapter at the edge,
  not the storage model. Filesystem coupling (real paths, path-traversal guards)
  ends at the wire shape -- `file` becomes an opaque org-scoped identifier.
- A migration script imports legacy data (SQLite + files) into Postgres. Run
  manually with Drew's go-ahead at cutover time; never automatic.

### 4. Audit logging: append-only table + emit hooks (compliance seam #1)

- `audit_events` table: `id BIGSERIAL`, `ts timestamptz DEFAULT now()`, `org_id`
  (nullable: pre-auth failures have no verified org), `actor_sub`, `action`,
  `resource_type`, `resource_id`, `detail JSONB`.
- **Immutable:** the `agilecards_app` role gets `INSERT` and `SELECT` only
  (no `UPDATE`/`DELETE`/`TRUNCATE` grant), plus a `BEFORE UPDATE OR DELETE`
  trigger that raises -- belt and braces, and the trigger also binds the owner.
- **Queryable:** `GET /api/audit` (admin role), org-scoped by RLS like any other
  tenant table. Events with `org_id NULL` (failed auth with no verified org) are
  operator-only by design -- no API surface returns them.
- Emitted on: auth failures, role denials, and every mutating route. This is the
  seam; downstream shipping (SIEM, retention) is deploy-time configuration.

### 5. Encryption at rest / TLS in transit (compliance seams #2, #3)

- **At rest:** AES-256 comes from full-volume encryption on the Postgres data
  volume (LUKS on Linux hosts, BitLocker on Windows), documented in
  `docs/security/DATA_PROTECTION.md` with the exact checklist per host type.
  Column-level pgcrypto is deliberately NOT used: no field in the card model
  meets the sensitivity bar, and per-column crypto breaks indexing/RLS ergonomics
  for zero charter gain. Seam = documented, verifiable host configuration.
- **In transit:** TLS terminates at the Cloudflare edge for the public route; the
  in-repo deploy also ships a reverse-proxy config (Caddy) that serves HTTPS with
  HSTS for direct/self-hosted deployment, so nothing in-tree listens plaintext
  beyond the compose-internal network.

### 6. SSE / live updates

An in-process `EventBus` (asyncio) behind a small interface; the SSE endpoint is
the one async route. Correct for the single-process uvicorn deployment alpha
actually uses. The interface leaves room for a Postgres LISTEN/NOTIFY
implementation when the deployment goes multi-process -- documented, not built.

### 7. Auth: unchanged (K11 JWKS verifier is kept as-is)

RS256-only JWKS verification against the Paradigm IdP stays authoritative;
`org_id` always comes from the verified token. For local/dev compose there is a
dev-only token mint script + static JWKS pair, enabled exclusively by explicit
env opt-in, so the full stack is provable end-to-end without the production IdP.

### 8. CI: the new backend's suite gates for real

The new backend job runs against a Postgres service container, with **no**
`continue-on-error`, and is intended as a required status check the moment this
branch merges (the audit's S1 lesson: green must mean protected). SBOM generation
(CycloneDX via syft) runs in CI (seam #6).

## Consequences

- Two backends still exist until the frontend cutover is approved; this branch
  builds and proves the new one but does **not** cut traffic (Drew's explicit
  go-ahead required -- Tier-3 rule).
- The legacy Express backend must be relabeled "active until cutover" instead of
  "delete after K11" (audit M1 landmine).
- RLS makes every future tenant table opt-in-by-default secure, but developers
  must remember: new tenant tables need the policy + FORCE + grants in their
  migration. A test asserts every `org_id`-bearing table has RLS enabled so CI
  catches the forgotten case.
