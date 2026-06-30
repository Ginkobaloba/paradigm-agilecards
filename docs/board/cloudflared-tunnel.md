# Cloudflare Tunnel for the dashboard

This repo's `docker-compose.yml` includes a `cloudflared` service that
exposes the frontend container at `https://app.projectnexuscode.org`
through a Cloudflare Tunnel.

## Topology

```
Internet
   |
Cloudflare edge (DNS + TLS + Access)
   |
   v
cloudflared connector  (runs in this compose, only on the 4070)
   |
   v
frontend  (nginx in compose, port 80 inside docker network)
   |
   v
backend   (express in compose, port 4070 inside docker network)
   |
   v
SQLite + card files on the host
```

The tunnel is account-side: an outbound TCP connection from the 4070 to
Cloudflare's edge. No inbound ports need to be opened on the host
network or the router.

## Identity

Cloudflare Access protects `app.projectnexuscode.org`. Only
`dramattick1@gmail.com` is allowed by the policy. Authentication is via
one-time PIN (sent to that email) using the built-in identity provider.

Behind Access, the dashboard's own bearer-token middleware still runs
on every `/api/*` and `/events` request. Two independent auth layers,
either of which fails closed.

To upgrade the IDP from one-time PIN to Google SSO, see
`docs/handoffs/HANDOFF_2026-05-17_tunnel-and-site.md` in
`projectnexuscode-site/` (the swap is a Zero Trust dashboard click,
zero code).

## Running locally

The tunnel is meant to run on the 4070 only. On any other dev box,
skip it.

### One-time setup on the 4070

```powershell
cd C:\dev\agile-cards\apps\board
Copy-Item .env.example .env
notepad .env  # paste the real TUNNEL_TOKEN
```

The token was issued during the 2026-05-17 tunnel-and-site session and
is documented in that handoff. Treat it like a password. If it ever
leaks, rotate it via:

```
DELETE /accounts/<acc>/cfd_tunnel/<tunnel_id>/token  (revoke + reissue)
```

### Day-to-day

```powershell
cd C:\dev\agile-cards\apps\board
docker compose up -d
docker compose logs -f cloudflared
```

Look for `Registered tunnel connection` lines from the connector. Once
those appear, hit `https://app.projectnexuscode.org` -- you should get
the Access one-time-PIN prompt, then the dashboard's token-gate login.

### Bringing the tunnel down without stopping the app

```powershell
docker compose stop cloudflared
```

The dashboard stays up locally at `http://localhost:8080`, just no
longer exposed to the public.

## Operational notes

- The tunnel ingress is configured account-side (not via a config.yml
  in this repo) so the routing lives in Cloudflare's API:
  `app.projectnexuscode.org -> http://frontend:80`, catch-all 404.
- The DNS record `app.projectnexuscode.org -> <tunnel_id>.cfargotunnel.com`
  is what tells Cloudflare's edge which tunnel to route to. That CNAME
  is proxied (orange-clouded) and managed in the projectnexuscode.org
  zone.
- The connector only needs outbound 443 to `*.cloudflareaccess.com` and
  `*.cloudflarewarp.com`. No router config.
- If you want to put the dashboard on a different subdomain later,
  PUT a new ingress rule via the tunnel configuration API, add a CNAME
  for the new hostname pointing at the same tunnel, and add an Access
  app for it. The tunnel itself is reusable.
