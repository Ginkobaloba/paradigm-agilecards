# Data protection: encryption at rest and in transit

Compliance seams #2 and #3 for the AgileCards stack (ADR-2026-07-16,
decision 5). This documents the seams -- concrete, verifiable host and deploy
configuration -- not certification machinery. Scope: the new stack in
`deploy/agilecards/` (FastAPI + Postgres 16 + Caddy).

---

## 1. Encryption at rest (seam #2): AES-256 full-volume

All persistent data lives in one place: the `pgdata` Docker named volume
backing `/var/lib/postgresql/data` in the postgres container. The control is
**full-volume encryption of the storage that volume sits on**, providing
AES-256 for everything Postgres writes (heap, WAL, temp files, logs) with
zero application changes.

### Why not column-level pgcrypto (rejected in the ADR)

- No field in the card model meets the sensitivity bar that would justify
  per-column cryptography (card titles, markdown bodies, sprint metadata).
- pgcrypto-encrypted columns are opaque to indexes and to the RLS policy
  expressions this architecture is built on; the ergonomic cost is real and
  the charter gain is zero.
- Full-volume encryption covers WAL and temp spill files, which column
  encryption does not; a pgcrypto design still needs volume encryption to
  close those, so it adds a layer without removing one.

### Checklist: Linux host (the normal deployment)

Either of the following satisfies the seam:

- **LUKS2 on the volume backing Docker's data root** (or a dedicated volume
  for `pgdata`):
  - Cipher `aes-xts-plain64` with a 512-bit key (= AES-256 in XTS mode; XTS
    splits the key, so 512 total). This is the `cryptsetup luksFormat`
    default on modern distros -- verify, don't assume.
  - Key management: passphrase or key file per host policy; never stored on
    the same volume it unlocks.
- **Cloud-provider encrypted volumes** (EBS/PD/Azure Disk with AES-256,
  which is their default): enable encryption on the volume at creation and
  record the KMS key used. Provider-managed keys satisfy the seam at alpha
  scale; customer-managed keys are a later decision, not a blocker.

**Verify (Linux):**

```bash
# Which device backs Docker's data root?
docker info -f '{{ .DockerRootDir }}'; df /var/lib/docker

# Is that device a LUKS mapping, and with what cipher/keysize?
lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINT
cryptsetup status <mapper-name>       # expect: cipher: aes-xts-plain64, keysize: 512 bits
cryptsetup luksDump <underlying-dev>  # LUKS2, AES, 512-bit keyslots
```

### Checklist: Windows host (Docker Desktop / dev-shaped deployments)

On Windows, Docker Desktop keeps volumes inside the WSL2 VM disk
(`ext4.vhdx`), which lives on the host NTFS volume -- so **BitLocker on that
host volume covers the Postgres data**.

- **BitLocker defaults to XTS-AES-128. The seam requires 256.** The cipher
  is fixed at encryption time, so set the policy BEFORE encrypting (an
  already-encrypted drive must be decrypted and re-encrypted to change it):
  1. `gpedit.msc` -> Computer Configuration -> Administrative Templates ->
     Windows Components -> BitLocker Drive Encryption -> "Choose drive
     encryption method and cipher strength (Windows 10 [Version 1511] and
     later)" -> Enabled -> **XTS-AES 256-bit** for operating system and
     fixed data drives.
  2. Then enable BitLocker on the volume holding the VHDX.
- Recovery key: escrowed per device policy (`DEVICE_SETUP.md`), never on the
  encrypted volume.

**Verify (Windows):**

```powershell
manage-bde -status C:
# "Encryption Method: XTS-AES 256" is the pass condition.
# "XTS-AES 128" means the policy was applied after encryption -- decrypt and
# re-encrypt, or the seam is not met.
```

### Out of scope for this seam

Postgres-native TDE (not in community Postgres 16), pgcrypto (rejected
above), and application-layer envelope encryption. Backups/dumps, when they
start existing, must land on an equally-encrypted volume -- note it in the
backup design, not here.

---

## 2. TLS in transit (seam #3): two postures

The deploy ships both; pick per host (`deploy/agilecards/Caddyfile`).

### Posture A: Cloudflare edge termination (default)

- A `cloudflared` tunnel connector runs next to the stack and makes an
  **outbound, mutually-authenticated TLS connection** to Cloudflare's edge;
  public clients terminate TLS at Cloudflare. No inbound port is opened on
  the host. Setup and hostname wiring: `docs/board/cloudflared-tunnel.md`.
- The web container listens plain HTTP (`:80`) **only on the compose-internal
  network**; the tunnel is its sole intended client.

### Posture B: direct Caddy HTTPS

- Uncomment the `cards.paradigm.codes` site block in the Caddyfile, publish
  443, point DNS at the host. Caddy provisions/renews ACME certificates
  automatically and serves with HSTS (6 months, includeSubDomains) plus the
  same security headers as the default site (`X-Content-Type-Options:
  nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
  `X-Frame-Options: DENY`).
- HSTS note: enable only once HTTPS is known-stable; browsers cache it.

### Inside the compose network: not TLS'd, accepted

web -> api (HTTP :8000) and api/migrate -> postgres (no `sslmode`) run
plaintext on the compose-internal bridge network. Accepted at alpha scale
because:

- All three endpoints are unpublished; the only path to them from outside
  the host is through the web container. Reading the traffic requires root
  on the Docker host, at which point TLS between containers defends nothing
  (the same root reads the certs and the data volume).
- Single-host deployment: no traffic crosses a physical link.

**Revisit trigger:** the moment any service moves to a second host (or a
managed Postgres), that link crosses a real network and MUST get TLS
(`sslmode=verify-full` for Postgres, HTTPS or mTLS for api). This is a
deploy-topology decision, not a code change.

---

## 3. The `/events?token=` exception

`EventSource` cannot set an `Authorization` header, so the SSE route -- and
only that route (`cards_api/deps.py::require_claims_header_or_query`) --
accepts the bearer token as a `?token=` query parameter. Legacy parity.

- **Risk:** query strings appear where headers do not -- reverse-proxy and
  edge access logs, and potentially browser history. A logged token is a
  replayable credential until it expires.
- **Mitigations:**
  - Short-TTL tokens for SSE connections (mint-side control; dev tool
    exposes `--ttl`, the IdP policy owns production lifetimes).
  - Edge log scrubbing: Cloudflare proxied-DNS logging excludes query
    strings from standard fields; if Logpush is ever enabled, exclude or
    redact the query string field for `/events`.
  - Caddy's default access log is not enabled in our Caddyfile; if access
    logging is turned on, add `log_skip` / field redaction for `/events`.
  - `Referrer-Policy: strict-origin-when-cross-origin` keeps the tokened
    URL out of outbound Referer headers.
- **Status: accepted for legacy parity.** Revisit with cookie-based auth for
  the SSE channel (or `fetch()`-based SSE with headers) when the frontend
  auth story is next touched.

---

## 4. Audit trail pointer (seam #1)

The append-only audit design lives in migration
`backend/migrations/versions/0001_initial_schema_rls.py`: `audit_events`
with INSERT/SELECT-only grants for the runtime role, org-scoped RLS
policies, and a `BEFORE UPDATE OR DELETE` trigger that raises for everyone
including the table owner. Emission points: auth failures, role denials,
every mutating route (`cards_api/audit.py`, `cards_api/deps.py`). Downstream
shipping (SIEM, retention schedules) is deploy-time configuration, out of
scope here.
