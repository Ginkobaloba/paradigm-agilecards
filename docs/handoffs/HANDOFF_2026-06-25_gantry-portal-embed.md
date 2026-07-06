# HANDOFF 2026-06-25 -- Gantry portal embed

Embed the agile-cards board in the Paradigm portal as the "Gantry" tile so
a logged-in portal user reaches the kanban with no second login. Work
spanned 2026-06-25 into 2026-06-26.

## What this session did

- **Designed** the embed: `/gantry/` base path, portal **JWKS federation**
  for auth (per `portal-shell/docs/PORTAL_GATE_CONTRACT.md`, same handoff
  the demo fleet uses), board as a `shape: subdomain` portal app so the
  tile routes through `/api/portal/launch/gantry` (signed-JWT fragment
  handoff).
- **Built and shipped the board side** (agile-cards PR #42, branch
  `feat/gantry-portal-embed`):
  - `backend/src/auth/portalToken.ts` -- verify portal RS256 JWTs vs JWKS
    (`iss`/`aud=gantry`/`exp`). New `PORTAL_JWKS_URL` / `PORTAL_ISSUER` /
    `PORTAL_AUDIENCE` config. Inert unless JWKS+issuer set; local SQLite
    tokens still work.
  - `frontend/src/lib/portalHandoff.ts` -- read `#portal_token`, store as
    bearer, scrub the fragment (+unit tests).
  - Brand: `VITE_APP_BRAND`/`VITE_APP_TAGLINE` (default `agile-cards`);
    the portal image builds with `Gantry`.
  - **Two Docker base-path bugs fixed** (the image had never run under a
    non-root base path): backend CMD `dist/server.js` -> `dist/src/server.js`
    (crash loop); frontend build copied to html root -> now under
    `${BASE_PATH}` (was nginx 500).
  - `docker-compose.gantry.yml` overlay (brand, base path, federation env,
    host port 8110).
- **Stood up the board live on <HOST>** (containers `gantry-board-frontend`
  :8110, `gantry-board-backend` :4070, mounts `C:/dev/todo`). Verified:
  `/gantry/` 200, `/gantry/healthz` 200, `/gantry/api/cards` 401 no-token /
  200 with board token.
- **Proved federation end to end** with a self-issued test JWT + ephemeral
  JWKS (real portal has no keys yet): valid JWT -> 200, payload-tampered ->
  401, garbage -> 401. So the auth design is verified; cutover is config.
- Tests: backend 96 pass, frontend 194 (+9 new). Typecheck clean.

## What is currently broken or incomplete (the blocker)

The **portal half cannot be cut over yet** because the portal is mid-migration
under a parallel task (`local_9deae09c`, "Portal migrate + rebuild password
auth"):

1. **`portal_signing_keys` is empty (0 rows)** -> JWKS returns `{"keys":[]}`
   -> the portal cannot mint or publish any `portal_token`. Federation is
   hard-blocked until the portal generates an active signing key.
2. **`APP_BASE_URL` (projectnexuscode.org) and `AUTH_URL` (paradigm.codes)
   disagree** -> the JWT `iss` (`= APP_BASE_URL`) is not yet settled.
   `portal.paradigm.codes` is the new canonical host (`portal-paradigm.conf`
   alias exists).
3. **portal-shell and cloudflare-config working trees are actively checked
   out** by the parallel tasks with heavy uncommitted changes (portal
   `feat/chunk-6-phase-b-signup-on-portal`: seed.ts, schema.ts, auth.ts,
   middleware.ts all dirty; cloudflare-config `infra/deploy-stripe-env-injection`).
   Editing them now would corrupt in-progress work, so the nginx route and
   the gantry seed entry are **staged below, not applied**.

No `gantry` app row exists in the portal `apps` table yet.

## PORTAL PREREQUISITE (Drew-gated) -- federation is broken portal-wide

Root cause confirmed 2026-06-26: the portal migration (PR #9) shipped
**without a JWT key-encryption secret and without a bootstrap signing key**.

- `PORTAL_JWT_KEY_ENCRYPTION_KEY` is NOT set in the running portal and NOT
  in `docker-compose.portal.yml` (only a placeholder in `.env.example`).
- `portal_signing_keys` is empty; `getActiveSigningKey()` throws when empty,
  so `/api/portal/launch/[slug]` **500s for every `subdomain`-shape app**
  (lumenanalytics, axlepoint, and Gantry) -- federation is non-functional
  in production right now, not just for Gantry.

A transient encryption key is NOT acceptable: it must persist in the deploy
config, or the next portal restart orphans the encrypted private key
permanently. One-time fix (Drew or the portal task, NOT done here):

```powershell
# 1. A candidate 32-byte secret is staged (gitignored, outside VCS):
#    C:\dev\_secrets\portal_jwt_key_encryption_key.local.txt
# 2. Add it persistently to docker-compose.portal.yml portal.environment:
#      PORTAL_JWT_KEY_ENCRYPTION_KEY: ${PORTAL_JWT_KEY_ENCRYPTION_KEY:?set me}
#    and export it (like AUTH_SECRET) for the deploy shell.
# 3. Recreate + mint the bootstrap signing key:
cd C:\dev\portal-shell
$env:PORTAL_JWT_KEY_ENCRYPTION_KEY = (Get-Content C:\dev\_secrets\portal_jwt_key_encryption_key.local.txt -Raw).Trim()
docker compose -f docker-compose.portal.yml up -d --force-recreate portal
docker compose -f docker-compose.portal.yml exec portal npm run gate:rotate
# verify: JWKS should now return a key, not {"keys":[]}
curl.exe -s -H "Host: portal.paradigm.codes" http://localhost:8090/.well-known/jwks.json
```

This is a portal-deploy fix (secrets-sensitive, outward-facing) and is the
gate for the Gantry cutover below. Surface to Drew first.

## What the next session should do first (cutover runbook)

Run only **after** the PORTAL PREREQUISITE above is satisfied and the portal
is federation-ready. Readiness check:

```powershell
# 1 = ready (>=1 active signing key), keys array non-empty, base url settled
docker exec portal-postgres psql -U portal -d portal -tAc "select count(*) from portal_signing_keys where status='active';"
curl.exe -s -H "Host: portal.paradigm.codes" http://localhost:8090/.well-known/jwks.json   # expect a key, not {"keys":[]}
docker exec portal-shell printenv APP_BASE_URL   # note this value -> it is the JWT iss
```

Then:

1. **Point the board at the real portal** (runtime env, no rebuild). Set
   `PORTAL_ISSUER` to the `APP_BASE_URL` value above and `PORTAL_JWKS_URL`
   to `<that origin>/.well-known/jwks.json`, then:
   ```powershell
   cd C:\dev\agile-cards\apps\board
   $env:BASE_PATH="/gantry/"; $env:CORS_ORIGIN="https://portal.projectnexuscode.org,https://portal.paradigm.codes"; $env:TUNNEL_TOKEN="unused"
   $env:PORTAL_ISSUER="<APP_BASE_URL>"; $env:PORTAL_JWKS_URL="<APP_BASE_URL>/.well-known/jwks.json"
   docker compose -f docker-compose.yml -f docker-compose.gantry.yml up -d --force-recreate backend
   ```
2. **Add the nginx `/gantry/` route** to the canonical portal server block
   (`cloudflare-config/nginx/conf.d/portal-paradigm.conf`, and
   `portal.conf` during transition). Insert the block in STAGED ARTIFACTS
   BEFORE the existing `location /`. Then reload: `docker exec demo-proxy
   nginx -s reload`. (portal-paradigm.conf is hand-maintained and safe to
   edit; portal.conf is Terraform-generated -- back the edit with the
   template change and let Drew run terraform apply.)
3. **Seed the gantry app** -- add the STAGED `upsertApp` block to
   `portal-shell/src/db/seed.ts` (reconcile with the post-migration schema;
   add a `visibility` field if the migrated schema has one), then
   `docker compose -f docker-compose.portal.yml exec portal npm run db:seed`.
   Or insert directly (see STAGED ARTIFACTS SQL) if reseeding is risky.
4. **E2E** with the audit-admin tester account (creds at
   `...\local_ditto_...\outputs\audit_tester_credentials.txt`): log into
   the portal, click the Gantry tile, confirm the JWT handoff and the
   kanban renders; confirm an unauthenticated `/gantry/` (no portal session
   and no token) shows the login/token gate.
5. **PRs**: cloudflare-config (nginx) titled per task; portal-shell (tile)
   titled "feat(portal): embed Gantry board at /gantry under auth gate".
   agile-cards PR #42 is already open.

## STAGED ARTIFACTS

### nginx `/gantry/` location (insert before `location /` in the portal block)

```nginx
    # Gantry (agile-cards board) embed. Routes /gantry/* to the board
    # frontend container; the board's own nginx handles /gantry/api and
    # /gantry/events internally. Variable proxy_pass + the server block's
    # resolver defers DNS to request time; no trailing URI so the full
    # /gantry/... path is preserved to the upstream.
    location /gantry/ {
        set $gantry http://host.docker.internal:8110;
        proxy_pass $gantry;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 60s;
    }

    # SSE stream: disable buffering so card updates push in real time.
    location = /gantry/events {
        set $gantry_sse http://host.docker.internal:8110;
        proxy_pass $gantry_sse;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 1h;
        chunked_transfer_encoding on;
    }
```

### portal seed entry (add inside seed.ts app section)

```ts
  await upsertApp({
    slug: "gantry",
    name: "Gantry",
    description: "Plan and track work as claimable cards on a live kanban.",
    url: "https://portal.paradigm.codes/gantry/",
    shape: "subdomain",     // -> tile links to /api/portal/launch/gantry (JWT fragment handoff)
    category: "internal",   // Paradigm's own tool: visible to staff/internal
    icon: "layout-grid",
    accentColor: "#1F9D57",
  });
```

### direct SQL fallback (if reseeding the live DB is risky)

```sql
INSERT INTO apps (slug, name, description, url, shape, category, icon, accent_color)
VALUES ('gantry','Gantry','Plan and track work as claimable cards on a live kanban.',
        'https://portal.paradigm.codes/gantry/','subdomain','internal','layout-grid','#1F9D57')
ON CONFLICT (slug) DO UPDATE SET url=EXCLUDED.url, shape=EXCLUDED.shape;
```

## Open questions for Drew

- **Brand name "Gantry"**: the task cited a `project_gantry_brand` memory
  that does not exist. Built as "Gantry" per the task; confirm or correct.
- **Audience**: Gantry seeded as `category: internal` (staff/internal only).
  If customers should see it, switch to `customer` + assign tenants.
- **Canonical host**: assumed `portal.paradigm.codes`. Confirm once the
  portal migration settles.

## Pointers

- agile-cards PR #42: board side (federation, brand, Docker fixes).
- Gate contract: `portal-shell/docs/PORTAL_GATE_CONTRACT.md`.
- Live nginx: `cloudflare-config/nginx/conf.d/portal-paradigm.conf` (canonical),
  `portal.conf` (transition); served by the `demo-proxy` container (:8090),
  conf.d mounted read-only from disk.
- Board overlay: `agile-cards/apps/board/docker-compose.gantry.yml`.
- Parallel task: `local_9deae09c` (portal migrate/rebuild) -- the gate.

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in
this project (and `portal-shell/CLAUDE.md` for the portal side), then this
file, then run `vstart`. The cutover runbook above is the resume point.
