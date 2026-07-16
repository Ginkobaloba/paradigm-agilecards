# ADR 2026-07-16 — Cards API: Postgres persistence with database-enforced RLS

**Status:** accepted (Drew's call, 2026-07-16: build the real backend, do not
ship alpha on legacy Express).
**Context:** the 2026-07-16 alpha-readiness audit
(`docs/audits/AUDIT_2026-07-16_alpha-gap-list.md`) found the FastAPI backend
was an auth-guarded skeleton over an in-memory dict while the product ran on
`legacy/board-express/`. This ADR records the architecture of the real
backend. The wire contract it implements is
`docs/board/CARDS_API_CONTRACT.md`.

## D1 — Serve the legacy wire contract, store in Postgres

The new backend speaks the exact API the existing frontend already consumes
(same routes, shapes, casing quirks), so the cutover is: point the frontend
at the new base URL and hand it a Paradigm JWT. The storage model changes
completely (markdown files + chokidar + SQLite sidecars -> Postgres rows);
the wire model does not.

*Rejected alternative:* a fresh "clean" resource-oriented API. It would force
a simultaneous frontend rewrite — exactly the big-bang coupling that stalled
K11 the first time.

## D2 — SQLAlchemy 2 (sync, psycopg 3) + Alembic

- Sync SQLAlchemy with FastAPI's threadpool for CRUD routes. Reasoning:
  simplest correct thing; TestClient-friendly; RLS mechanics identical to
  async; alpha throughput is nowhere near threadpool limits. The repository
  layer isolates the choice — an asyncpg migration later touches one module.
- Alembic owns the schema. Migrations are raw-SQL-first (RLS policies,
  grants) because policies are the security boundary and must be reviewable
  as SQL, not generated diffs.
- SSE is async (asyncio bus); sync routes publish into it via
  `loop.call_soon_threadsafe`. Single-process only in v1 — documented in D7.

## D3 — Row-level security that actually binds

The audit's S2 finding was blunt: "org isolation" was a Python list
comprehension. The fix has to hold even when application code is wrong.

- Every tenant table carries `org_id TEXT NOT NULL` and gets
  `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY` (force means
  even the table owner obeys policies).
- The application connects as **`cards_app`** — `LOGIN`, `NOSUPERUSER`,
  `NOBYPASSRLS`, and **not** the owner of any table. Postgres only enforces
  RLS on non-owner roles unless forced; we do both (belt + suspenders).
- Policies:
  `USING (org_id = current_setting('app.current_org', true))
   WITH CHECK (org_id = current_setting('app.current_org', true))`.
  With no org set, `current_setting(..., true)` is NULL and the policy
  matches nothing — fail closed.
- Each request transaction begins with
  `SELECT set_config('app.current_org', :org, true)` (transaction-local, so
  pooled connections cannot leak an org across requests). The org value comes
  only from the verified JWT.
- App-layer scoping (`WHERE org_id = ...` in repositories) is **kept** as the
  first line; RLS is the backstop that turns a forgotten WHERE into zero rows
  instead of a cross-tenant leak.
- Migrations/DDL run as the database owner via a separate
  `CARDS_MIGRATIONS_DATABASE_URL`; the app's `CARDS_DATABASE_URL` must be the
  `cards_app` role. Tests assert `cards_app` cannot bypass the policies even
  with raw SQL.

*Rejected alternatives:* schema-per-tenant (operational overhead for an alpha
with small orgs, painful migrations x N); separate DB per tenant (same,
worse); trusting app-layer filtering alone (the finding we are fixing).

## D4 — Audit log: immutable + queryable (cheap seam, not certification)

- `audit_events` table: `id, org_id, at, actor_sub, action, resource_type,
  resource_id, outcome, details jsonb`.
- Immutability by privilege: `cards_app` gets `INSERT` and `SELECT` only —
  no UPDATE/DELETE grant, plus RLS org scoping on reads. Retention/erasure is
  an owner-role operation, deliberately outside the app's power.
- What is recorded: every mutation (card create/move/patch, rank set, view
  and sprint and triage changes), every authentication failure, every role
  denial. Auth failures have no verified org; they are written under the
  sentinel org `__system__` (readable only by operators, not through the API).
- Queryable: `GET /api/audit` (admin role) reads the caller's own org's
  trail. Also mirrored to structured stdout logs (S4) so a log shipper can
  pick it up without DB access.
- Explicitly NOT built: SSP/POA&M documents, compliance dashboards,
  certification tooling — out of charter ("cheap seams" posture).

## D5 — Encryption at rest = storage layer, documented, not app-layer crypto

AES-256 at rest is provided by the storage under Postgres, not by encrypting
columns in the app:

- Managed path (recommended for the real deploy): RDS/Cloud SQL/Neon-style
  storage encryption (AES-256) switched on at instance creation.
- Self-host path (the compose file): the Postgres volume sits on an
  encrypted disk (BitLocker/LUKS/dm-crypt). `deploy/README.md` states the
  requirement and the verification command.

*Rejected alternative:* pgcrypto/app-layer field encryption. It breaks
indexing/querying on frontmatter, adds key management the alpha does not
have infrastructure for, and protects against the same threat (stolen disk)
that volume encryption already covers. Escalation path if a real
requirement lands: pgcrypto on `cards.body` only.

## D6 — TLS in transit

Terminated at the edge (Cloudflare tunnel, as with the legacy deploy) or at
the bundled reverse proxy. The compose ships a Caddy front that serves HTTPS
with HSTS; `deploy/README.md` documents both postures. Nothing in the app
speaks plaintext to anything but localhost/compose-network peers.

## D7 — SSE bus is in-process, org-keyed

Asyncio pub/sub keyed by org_id; SSE connections only ever see their own
org's events. Single-process limitation (multiple uvicorn workers would
fragment the bus) is accepted for alpha and documented; the scale-out path
is Postgres `LISTEN/NOTIFY` behind the same bus interface.

## D8 — Scope cuts (deliberate, flagged)

- **Stories submit/approve/cancel: not ported.** The legacy implementation
  shells out to the `claude` CLI from the web process. The audit (P2) is
  right that this belongs behind the engine runner — one execution engine,
  one verifier, one ledger. Porting the CLI-spawn into the new backend would
  cement the wrong architecture. The routes return 501 with a pointer; the
  board's SubmitStory page keeps working against legacy until the P2 arc.
- **Frontend cutover is a separate PR.** This backend reaches contract
  parity first; flipping `frontend/` to it (base URL + Paradigm JWT in
  TokenGate + healthz field tweak) is its own reviewable change, and the
  legacy backend keeps serving testers until Drew calls the cutover.
- **Retros are ported** (trivial CRUD; closes the parity gap even though the
  frontend page is still a placeholder).

## D9 — Config & secrets

Extends the existing K11 Infisical/env pattern (`cards_api/config.py`):
`CARDS_DATABASE_URL` (app role), `CARDS_MIGRATIONS_DATABASE_URL` (owner,
deploy-time only). No credentials in the repo; compose reads them from env /
`.env` (gitignored), CI uses throwaway service-container credentials.
