# Upgrading from a quick tunnel to the persistent named tunnel

This is the manual checklist for swapping the dashboard from an ephemeral
`cloudflared tunnel --url ...` quick tunnel (random `*.trycloudflare.com`
hostname, rotates every restart) over to the persistent named tunnel
configured in `.env.example` and `docker-compose.yml`.

Most of these steps need Drew's Cloudflare account access -- they cannot
be automated from inside this repo.

- **Tunnel name:** `agile-cards-board-tunnel`
- **Tunnel ID:** `<YOUR_TUNNEL_ID>` (look up in Zero Trust -> Networks -> Tunnels)
- **Account ID:** `<YOUR_ACCOUNT_ID>` (visible in the dashboard URL once logged in)
- **Target public hostname:** `app.projectnexuscode.org` (already wired in
  the compose file's `cloudflared` service comments)

## 0. Decide which machine hosts the tunnel

Only one machine should run the `cloudflared` connector at a time
(currently the 4070 Super). Run the checklist from there. Other devices
in the dev pool continue to use `localhost:5173` directly.

## 1. Pull the connector token from Cloudflare Zero Trust

1. Open the Cloudflare dashboard, switch to the right account.
2. Navigate to **Zero Trust -> Networks -> Tunnels**.
3. Find `agile-cards-board-tunnel` in the list. Click **Configure**.
4. On the **Overview** tab there is a "**Token**" reveal control. Copy
   the full token string (it is long, starts with `eyJ...`).
5. Treat the token like a credential. It grants the holder the right to
   register a connector for this tunnel and serve traffic for the
   tunnel's configured hostnames. Never paste it into chat, a public
   gist, or anything that gets indexed.

Alternative (API): with an account-scoped token that has
`Cloudflare Tunnel:Read`, hit
`GET /accounts/<YOUR_ACCOUNT_ID>/cfd_tunnel/<YOUR_TUNNEL_ID>/token`.

## 2. Set the DNS route / public hostname

In the same tunnel config page, open the **Public Hostnames** tab.

If `app.projectnexuscode.org` is already listed and routes to
`http://frontend:80` (or `http://localhost:8080` for a non-docker run),
skip this step.

Otherwise:

1. Click **Add a public hostname**.
2. **Subdomain:** `app`
3. **Domain:** `projectnexuscode.org` (must be a zone Cloudflare already
   manages on this account; if not, add the zone first under **Websites**)
4. **Type:** HTTP
5. **URL:** `frontend:80` if you're running the docker-compose stack
   (this is the in-network hostname of the `frontend` service). For a
   bare-host run, use `localhost:8080` or whatever port the nginx
   container or local dev server is listening on.
6. Save. Cloudflare will create the DNS CNAME automatically. Propagation
   inside the Cloudflare edge is usually a few seconds.

## 3. Drop the token into `.env`

On the chosen host machine, inside this repo:

```powershell
Copy-Item .env.example .env -ErrorAction SilentlyContinue
# then edit .env and replace `replace-with-tunnel-token` with the token
```

The `.env` file is already gitignored. Confirm with
`git check-ignore .env` before saving anything secret to it.

## 4. Bring it up

Two options. Pick one. Do not run both at the same time -- the tunnel
will refuse a second connector with the same token.

### Option A: docker compose (recommended, matches the rest of the stack)

```powershell
docker compose up -d cloudflared
docker compose logs -f cloudflared
```

The container should log `Connection registered` and `Registered tunnel
connection` within a few seconds. If it loops on `Unable to reach the
origin service`, the public-hostname URL in step 2 is wrong (most likely
pointing at a hostname that doesn't resolve inside the compose network).

### Option B: `cloudflared service install` (bare-metal Windows service)

For a setup where the dashboard runs without docker:

```powershell
# Install cloudflared as a Windows service that auto-starts on boot.
cloudflared.exe service install <TUNNEL_TOKEN>
# Verify it's running.
Get-Service cloudflared
```

The service writes its logs to
`C:\Windows\System32\config\systemprofile\.cloudflared\` by default.

To remove later: `cloudflared.exe service uninstall`.

## 5. Verify end-to-end

1. `https://app.projectnexuscode.org` should load the dashboard.
2. The browser dev tools "Network" tab should show `cf-ray` and
   `cf-cache-status` response headers on the document request --
   confirms the request actually went through Cloudflare's edge, not a
   direct connection.
3. Hit `/healthz` -- it should return `{ "ok": true }` from the backend
   via the nginx -> backend proxy chain.
4. SSE: open the kanban, watch the browser's "EventStream" panel on the
   `/events` request. It should stay open and emit `card-*` events when
   files change in the cards dir.

If step 1 returns Vite's "Blocked request" page, the `allowedHosts`
entry for `app.projectnexuscode.org` was not picked up -- restart the
dev/frontend container so the new `vite.config.ts` takes effect.

## 6. Decommission the old quick tunnel

Once the named tunnel is verified end-to-end:

1. Find the `cloudflared tunnel --url ...` process from the previous
   session (4070 Super, started outside docker compose). It is usually
   running under a terminal that the previous session left open.
2. Stop it (Ctrl-C in that terminal, or `Stop-Process` if it's
   detached).
3. The random `*.trycloudflare.com` URL it was serving will go dead.
   That's expected -- everything moves to
   `app.projectnexuscode.org` now.

## Failure modes worth knowing

- **`TUNNEL_TOKEN is required`** at docker compose up -- `.env` isn't
  in the current directory, or the variable isn't named exactly
  `TUNNEL_TOKEN`.
- **Connector registers but the public hostname returns 502** -- step 2
  points at a URL the connector can't reach. From inside the
  `cloudflared` container, `wget -qO- http://frontend:80` should
  return the nginx index. If it doesn't, the `frontend` service isn't
  up or isn't on the same compose network.
- **Cloudflare Access blocks Drew's own browser** -- expected if Access
  is enabled and the email isn't in an allowed policy. Fix in the
  Zero Trust dashboard under **Access -> Applications**.
- **Tunnel ID in `.env.example` doesn't match what's in the dashboard**
  -- the tunnel was recreated. Update `.env.example` and this doc
  with the new ID and rerun step 1.
