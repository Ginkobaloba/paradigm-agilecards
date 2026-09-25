# AgileCards — Alpha Readiness Audit & Prioritized Gap List

**Date:** 2026-07-16
**Audited commit:** `origin/main` @ `7c9ce99` ("degrade gracefully on a non-dict provider response (KL2 review)", #53)
**Repo:** `Ginkobaloba/paradigm-agilecards` (the live monorepo; `agile-cards` on disk is an empty husk, `agile-cards-board` is the superseded pre-monorepo repo)
**Method:** six parallel lens agents, each in its own detached git worktree off `origin/main` (feature-completeness, code-quality, compliance-seams, test-coverage, UX, deploy-readiness). Every claim below is verified against actual current code and, where noted, actual test/build runs. Status docs, sprint plans, and prior handoffs were treated as untrusted and re-verified.
**Audience:** internal. Drew asked for the real state, no sugarcoating. This is that.
**Effort key:** **S** ≤ 1 day · **M** 1–3 days · **L** ~1–2 weeks · **XL** > 2 weeks. Estimates are for a single focused engineer/agent and are deliberately rough.

---

## 0. The one-paragraph truth

AgileCards is in a strange and specific place: the **product is more built and more polished than a pre-alpha usually is**, and the **path to actually putting it in front of a tester is less built than the status docs imply**. The signed-in board (kanban, triage inbox, saved views, command palette, manual rank, card-event timeline, sprint planner + capacity, 2D stakes×difficulty grid, backlog grooming) is real, wired, and unusually clean — 1,100 tests pass across the repo, the frontend and backend both compile, and the engine/runner is senior-grade code. But the working product runs **entirely on the `legacy/board-express/` backend that the repo instructs you to delete**, over that backend's SQLite, while the "real" `backend/` (FastAPI) that was supposed to replace it is a **thin auth-guarded skeleton over an in-memory dict with no card CRUD and no persistence**. There is **no deploy artifact for the current stack at all** — every Dockerfile/compose/route in the tree builds the old Express app for the old host. So the honest summary is: *the thing is good; the thing cannot currently be deployed, and the two halves of "the thing" disagree about which backend is real.* Alpha is blocked on decisions and plumbing, not on product quality.

---

## 1. The spine: one architectural fact drives half this list

Everything in the MUST-FIX tier radiates from a single reality, so it's worth stating once, plainly, before the tiers:

**The board's real backend is `legacy/board-express/` (Express/TS, ~4,943 LOC, full CRUD: cards/columns/ranks/rates/sprints/stories/triage/views/retros/SSE, SQLite-backed, path-traversal-guarded, 96 passing tests). The frontend talks to it exclusively** — `frontend/src/lib/api.ts` types a card as `{file, frontmatter, mtimeMs, body}` and calls ~20 routes plus SSE. **The "new" `backend/cards_api/` (FastAPI, ~490 LOC) serves only `GET/POST /api/cards` over an in-memory dict (`store.py:31`), with an incompatible card shape `{id, org_id, title, status}` and even a different `/healthz` body.** The board literally cannot run on it. Yet `README.md:38`, `DECISIONS.md`, and the CI job comments all describe legacy as *"frozen, delete after K11"* — and K11 (#47) shipped, but it delivered only the JWKS auth + org-isolation contract, **not** the card-CRUD rewrite or the frontend cutover. The migration is roughly 10% done and labeled complete.

Consequences that appear as separate findings below but are all this one fact: no deployable current stack; "delete after K11" is a landmine that would delete the running product; the excellent auth/org-isolation tests guard a store that holds no real cards; RLS/encryption-at-rest have nothing to attach to; the live board path has no JWKS/multi-tenant isolation story.

**This forces the first decision (see §6): which backend does alpha ship on?** Nearly every effort estimate below forks on that answer.

---

## 2. Compliance seam scorecard (the locked "cheap seams" posture)

Verified in code, not docs. Charter reminder: this is about the *seams existing*, not certification machinery (no SSP/POA&M/dashboards — explicitly out of scope). Backend security tests (32) all pass and assert what they claim; the auth contract is real.

| # | Seam | Verdict | Evidence | Note |
|---|------|---------|----------|------|
| 1 | Audit logging (immutable + queryable) | **ABSENT** | No logging on any path in `backend/cards_api/` | Auth failures, role denials, card mutations all vanish. Cheapest high-value seam to add. |
| 2 | Encryption at rest (AES-256) | **ABSENT** | `store.py:31` in-memory dict; no DB anywhere | Nothing is persisted, so nothing is at rest. Blocked by the persistence gap, not a real config choice yet. |
| 3 | TLS in transit | **ABSENT in-repo** | Only proxy is legacy `nginx.conf:15` `listen 80` (plaintext, slated for deletion) | Presumably terminated at the Cloudflare edge (out of repo). Nothing in-tree enforces HTTPS/HSTS/secure-cookie. |
| 4 | RBAC row-level (Postgres RLS) | **PARTIAL** | Roles real + tested (`deps.py:54` `require_roles`, `main.py:82`); org scoping is `store.py:36-48` list-comprehension filter | **RLS: none.** No Postgres, no `CREATE POLICY`, no `SET app.current_org`. Isolation is app-layer only. Do **not** credit "org isolation (K11)" as row-level security. |
| 5 | MFA hooks | **PARTIAL** | Auth fully delegated to Paradigm IdP via RS256 JWKS (`auth.py:58-131`) — the correct upstream seam | No MFA-specific code; WorkOS trigger is a stub reference to a doc that "does not exist yet." Nothing to build here, but don't mistake the stub for a wired hook. |
| 6 | SBOM generation | **ABSENT** | No sbom/cyclonedx/syft in tree or CI | One CI step fixes it. |
| 7 | FIPS-capable crypto | **PRESENT** | All crypto via `pyjwt[crypto]` → OpenSSL-backed `cryptography` (`pyproject.toml:15`, `auth.py:96`); no hand-rolled crypto | Capable ≠ mode-enabled; nothing pins a FIPS provider. Fine as-is for the charter. |
| 8 | Externalized secrets (no inline creds) | **PRESENT** | `config.py:56-103` Infisical (Universal-Auth machine identity) or env; `.env.example` placeholders only; `test_config_secrets.py:66` asserts no committed `.env` | Strongest seam in the set. Repo-wide grep found zero hardcoded production secrets. |

Read: **2 present, 2 partial, 4 absent** — but the four "absent" are all cheap-to-seam and three of them (audit log, encryption-at-rest, RLS) only become *real* once persistence lands. The seam charter is largely honored on the parts that exist today; the gaps are downstream of the missing datastore.

---

## 3. Test & CI reality (what "green" actually means)

Test **quality and quantity are genuinely senior-level** — this is a strength, stated plainly. All suites were run:

| Suite | Files | Result | What it really covers |
|-------|-------|--------|-----------------------|
| `engine/runner/tests` | 60 | **713 pass** | Daemon lifecycle, merge/AC gate, SQLite store CRUD + 8-thread concurrency races, providers, cost governor, calibration, PR lifecycle, reaper/orphan. Strong assertions + failure paths. |
| `backend/tests` (FastAPI) | 5 | **32 pass** | JWKS RS256 verify, HS256-confusion rejection, iss/aud/exp/nbf, org isolation, RBAC, secrets. Excellent auth tests. |
| `backend/contracts` | 1 | **9 pass** | `@paradigm/auth` Python consumer contract. |
| `frontend/src` | 20 | **194 pass** | Zustand store, filters/lens/saved-view/WIP, grid/dep-graph/capacity/similarity libs, route components (RTL). |
| `frontend/contracts` | 1 | **4 pass / 2 skip** | `@paradigm/llm-client` — the **live conformance block is `describe.skip`**. |
| `engine/lib/verifier/tests` | 7 | **52 pass** | The `/cards` skill's reference verifier — **not in CI**, a **divergent copy** of the runner's verifier. |
| `legacy/board-express/backend` | 9 | **96 pass** | The **currently-live** board API. |

**Totals: 1,100 pass, 0 fail, 2 skip.** No suite is a bare smoke test. **But "CI green" materially overstates what's protected**, for two config reasons (both MUST/SHOULD below): the live board backend's tests run `continue-on-error: true` (green even when failing), and the security-critical FastAPI auth/isolation suite is **not a required status check**. Also: **zero coverage measurement** anywhere (no pytest-cov / vitest coverage), so 1,100 tests of unknown line/branch reach.

---

## 4. THE PRIORITIZED GAP LIST

Tiers are defined by the alpha bar: **Must-fix = alpha cannot start / would mislead or lose tester data. Should-fix = strongly recommended before or during early alpha. Small tweaks = low-effort polish. Powerful features = roadmap-level, explicitly not required for alpha.**

Several MUST-FIX items are *conditional* on the two open decisions in §6 — flagged inline.

---

### TIER 1 — MUST-FIX (blocks alpha testing)

**M1. Resolve the backend fork — the product runs on the "delete-me" backend; the "real" backend is an empty shell.**
*Evidence:* §1. Frontend → legacy Express (`api.ts:23-28` + ~20 routes); FastAPI `cards_api/main.py:47-89` = 5 routes over `store.py:31` in-memory dict; README/CI say "delete after K11"; #47 delivered auth only.
*Why it blocks alpha:* "deploy AgileCards" is undefined until you choose which backend serves testers. Every downstream deploy, persistence, and security decision forks here. Worse, the literal "delete after K11" instruction, if followed, deletes the running product.
*What breaks / improves:* pick a lane and deploy becomes a definable task; leave it ambiguous and no one can safely deploy *or* clean up, and the repo keeps carrying two backends that disagree.
*Effort:* the **decision** is S (Drew, see §6.1). The **consequence** forks hard: ship-legacy ≈ **M** (wire a deploy for the Express+Vite stack, relabel legacy from "frozen/delete" to "active"); ship-FastAPI ≈ **XL** (build CRUD parity — columns/ranks/rates/sprints/stories/triage/views/SSE — plus persistence plus the frontend contract cutover; this is the bulk of the original K11 that never happened).

**M2. No deploy artifact exists for the current stack; the only compose targets the old host.**
*Evidence:* every Dockerfile/compose/nginx lives under `legacy/board-express/`; `docker-compose.yml` builds Express on port 4070, `DB_PATH /data/board.sqlite`, bind-mounts `C:/dev/todo`, host `app.projectnexuscode.org`. The FastAPI `backend/` and Vite `frontend/` have **no Dockerfile, no compose, no nginx, no deploy script, no IaC**. No deploy job in `ci.yml`/`verify.yml`. Roadmap states the app "has never been deployed to a live URL."
*Why it blocks alpha:* there is no reproducible way to stand up `cards.paradigm.codes`. Someone has to hand-write container + serving + tunnel-route config before a single tester can reach the product.
*What breaks / improves:* with an artifact, alpha deploy is a repeatable command and rollbacks exist; without it, every "deploy" is a bespoke manual act with no record.
*Effort:* **M** for the ship-legacy path (adapt the existing legacy compose to the new host/route + TLS + a real DB volume). **M–L** for ship-FastAPI (net-new Dockerfiles for FastAPI + the Vite static build + the Node BFF the roadmap's K11b calls for, none of which exists).

**M3. Deploy target is contradictory across docs and the smoke gate is written for the wrong backend.**
*Evidence:* `README.md:7` says `cards.paradigm.codes`; `README.md:179` + `docs/board/cloudflared-tunnel.md` say `app.projectnexuscode.org`; `verify/smoke.yml` header says `portal.paradigm.codes/gantry`. `smoke.yml` asserts `$.ok == true` and `/api/columns → 401`, but the FastAPI backend returns `{"status":"ok"}` and `/api/columns → 404` (both verified live). Currently masked only because `deploy_url` is a placeholder that skips the job.
*Why it blocks alpha:* you cannot tell a tester where to go, and the moment a real `deploy_url` is set, the health gate fails a *healthy* service — so your one automated safety check is actively wrong.
*What breaks / improves:* one canonical URL + a smoke config matching the chosen backend's actual shapes gives you a trustworthy go/no-go signal.
*Effort:* **S**, once M1/M2 are settled (pick one URL; fix three docs + the smoke assertions).

**M4. (Conditional — if the marketing landing is in alpha scope) It ships the reverted Gantry brand end-to-end and a signup form that fakes success.**
*Evidence:* `marketing/index.html` (title/description/canonical `gantry.projectnexuscode.org`/OG/Twitter/favicon), every `marketing/src/components/*.tsx`, the whole `gantry-*` Tailwind token namespace, `marketing/package.json:2`. `FinalAction.tsx:14-21`: submitting an email only `console.info`s, then displays "you are on the list."
*Why it blocks alpha (if shipped):* a tester sees a product name ("Gantry") that doesn't match the app they log into ("AgileCards"), and the waitlist form **tells them they succeeded while capturing nothing** — deceptive, and every signup is lost. The Gantry rebrand was reverted 2026-06-28; this is dead-brand debt, not intentional.
*What breaks / improves:* excluding or fixing the page removes a credibility-killer on the first surface a prospect sees.
*Effort:* **S** to gate the marketing route out of alpha scope (the clean call unless the landing is needed); **M** to actually re-brand the site to AgileCards + wire a real form endpoint if it's kept. Decision §6.2.

---

### TIER 2 — SHOULD-FIX (strongly recommended before/at alpha; not hard blockers)

**S1. CI "green" doesn't protect the two things that matter most — trivially cheap to fix.**
*Evidence:* `ci.yml:107-109` runs the live board-backend's 96 tests with `continue-on-error: true` (green even when they fail; only `tsc` gates); `ci.yml:111-135` leaves `backend-fastapi` (the auth/org-isolation suite) as a **non-required** context.
*Why it matters:* during alpha you'll iterate fast with testers watching. A regression in the live board, or in token verification / cross-org isolation, can merge green. The tests already exist and pass — this is pure gate config, and it's the **highest ROI item in the whole audit**.
*Effort:* **S** (remove `continue-on-error`; add both as required branch-protection contexts). Note: solo-account branch protection here is notify/CODEOWNERS-style, not review-required — consistent with the established Tier-3 posture.

**S2. Org isolation is app-layer only; make RLS a hard requirement on the persistence chunk now.**
*Evidence:* `store.py:36-48` filters by `org_id` in Python; roles are real and tested, but there's no DB and no RLS. One forgotten `WHERE org_id = …` in the future CRUD rewrite is a cross-tenant leak.
*Why it matters:* a multi-tenant alpha's core promise is that Org A never sees Org B's cards. Today that promise rests on a list comprehension. It holds (9 isolation tests pass) but has no backstop.
*Effort:* **S now** (write it down as a binding requirement on the CRUD/Postgres work); **M later** (author the RLS policies when the datastore lands).

**S3. Add the audit-log seam — the cheap hook that's painful to retrofit.**
*Evidence:* zero logging on `auth.py`, `deps.py:34-65`, `main.py:79-88`.
*Why it matters:* auth failures, role denials, and card creation are exactly the security-relevant events an audit trail needs, and right now they leave no trace. After alpha traffic, reconstructing "who did what" is impossible. The charter explicitly wants this seam.
*Effort:* **S** — a structured `logger.info(...)` emitting `sub`/`org_id`/action on three code paths. Immutable/queryable store can come later; the *hook* is the point.

**S4. No structured logging / error tracking / metrics in the runtime.**
*Evidence:* `backend/cards_api/*` has no application logging (uvicorn access lines only), no Sentry, no metrics/OTel. (The engine runner has rich logging, but that's the internal card-builder tool, not the product runtime.)
*Why it matters:* a 500 during alpha leaves no breadcrumb. You'll be debugging tester reports blind.
*Effort:* **S–M** (structured logging config + an optional Sentry DSN gated by env).

**S5. AgileCards emits no `paradigm-status/v1` report → it's invisible to the ops dashboard.**
*Evidence:* no `docs/status/` dir; `paradigm-ops` ingests `docs/status/STATUS_*.md` via the GitHub API (`src/lib/reports.ts`), **not** runtime endpoints. The velocity rules already mandate the report.
*Why it matters:* "is it monitored?" for a Paradigm app means "does it show up in paradigm-ops?" — and the flagship currently doesn't. This is the cheapest possible monitoring win.
*Effort:* **S** (commit one `STATUS_*.md`; automate at session-end later).

**S6. Two verifier trees are drifting, and the "single source of truth" doc claim is false.**
*Evidence:* `engine/SKILL.md:106,525` + `RUNNER_CONTRACT.md:369` declare `engine/lib/verifier/types.py` canonical, but the daemon ships and runs its own parallel `engine/runner/src/cards_runner/verifier/` and never imports `lib/verifier`. `lib/verifier` is **not in CI** and is already **lint-dirty** (6 ruff errors incl. `F821 Undefined name 'Path'`).
*Why it matters:* the skill's reference verifier and the live verifier can diverge silently — a rotting second copy is worse than none, and it falsifies a documented guarantee.
*Effort:* **S–M** — either wire `lib/verifier` into CI and fix its lint, or delete it and point SKILL.md at the runner's copy. Pick one; don't keep both unowned.

**S7. Local-GPU / multi-provider execution is not wired end-to-end.**
*Evidence:* provider adapters exist and are unit-tested (reasoning-only cross-provider works), but `providers/base.py:11-13` says tool-use is KL3 and unbuilt; `sdk_invoker.py`'s `_run_tool_loop` calls the Anthropic client directly; no caller passes `provider="local"` to `load_tier_map`; `spawner.py:99-114` injects only `ANTHROPIC_API_KEY` and never the local base-URL/tier-map through the env scrub. No per-card `route:`/`provider:` field is consumed anywhere.
*Why it matters:* the headline "run Cards on my 4090 when I'm out of tokens" only works for reasoning-only "report on the work" turns — a local model **cannot** actually edit files or run tools today, and provider selection is a global env toggle the worker can't even receive. If local-GPU is an alpha selling point, it isn't real yet; if it's roadmap, this is fine to defer.
*Effort:* **L** (the KL3 tool-turn port across providers + per-card routing + spawner env passthrough).

**S8. The merge gate is inert by default — cards land "merged" with no branch, PR, CI, or review.**
*Evidence:* `merge_gate.py:144-154` defaults `pr_gate_enabled=False`, returning "verifier pass → done, `merge_status=merged`, skipped=True"; `daemon.py:1068-1073` confirms no PR was routed. The real `gh` flow exists but no shipped config enables it.
*Why it matters:* the tiered auto/sibling/human routing the contract advertises is real code but does nothing unless a flag is flipped that isn't set anywhere. For an alpha where the runner is a selling point, the gate silently no-ops.
*Effort:* **S** to flip + configure; but verify the `gh` path end-to-end first (**M**) before trusting it in front of testers.

**S9. mypy-strict debt in the runner hides 2–3 likely-real bugs behind `continue-on-error`.**
*Evidence:* `cd engine/runner && mypy` → 24 errors in 17 files. Real-bug candidates: `common/process_group.py:127,134` (`os.killpg`/`getpgid`/`SIGKILL` — POSIX-only APIs in a Windows-developed repo; unclear if guarded), `daemon/pr_lifecycle.py:366,368` (`.decode` on a `bytes | str` union — crashes if a `str` arrives), `daemon/daemon.py:520` (`AmendmentOutcome` assigned to a `ReviewOutcome` variable — type confusion). Plus 10 stale `type: ignore`.
*Why it matters:* the runner is otherwise the strongest code in the repo; these three are plausible latent crashes, and they're invisible because the check is informational.
*Effort:* **S** to triage/fix the 2–3 real ones; **M** to clear all 24 and re-gate.

**S10. SubmitStory's project picker is hardcoded to Drew's machine.**
*Evidence:* `routes/SubmitStory.tsx:39-44` — `PROJECT_OPTIONS` literals `C:\dev\agile-cards`, `C:\dev\agile-cards-board`, `C:\dev\project-example`.
*Why it matters:* any tester not on Drew's filesystem sees dead, machine-specific paths — the submit-story feature is unusable for them.
*Effort:* **S** (serve the project list from the backend/health/config endpoint).

**S11. The board shows "agile-cards-board", not the canonical "AgileCards / Agile Boards".**
*Evidence:* `index.html:8` `<title>agile-cards-board`; `TokenGate.tsx:52` login heading `agile-cards-board`; `lib/brand.ts:18,23` default `APP_BRAND="agile-cards"`, tagline `"board v0+"`; storage key `agile-cards-board.token`.
*Why it matters:* the very first screen a tester sees (tab + login) carries the wrong, pre-brand name. Cheap, and it's a portfolio surface.
*Effort:* **S** (title + TokenGate copy + `brand.ts` defaults + `VITE_APP_BRAND`).

**S12. The frontend has no linter despite a CI job literally named "lint".**
*Evidence:* `ci.yml` job "board frontend battery (lint + vitest)" runs `npm run lint --if-present`, but `frontend/package.json` has **no `lint` script and no eslint dependency** — the step silently no-ops. `App.tsx:140` even carries an `// eslint-disable-next-line` for a rule nothing enforces.
*Why it matters:* the React/TS surface — a portfolio artifact — has zero static linting, and the CI job name lies about it. (Credit where due: tsconfig is strict, `tsc --noEmit` is clean, and there are **0** `any`/`as any` in non-test `frontend/src` — the type hygiene is genuinely good; it's the lint layer that's missing.)
*Effort:* **S** (add eslint + a flat config; wire the existing job).

**S13. Ruff configs are near-empty, so "all checks passed" is a weak signal.**
*Evidence:* both `backend/pyproject.toml` and `engine/runner/pyproject.toml` set only `line-length` — no `[tool.ruff.lint] select`, so only the default `E/F` set runs. There are 85 `# noqa: BLE001` suppressions for a `BLE` rule that **isn't even selected** (vestigial).
*Why it matters:* the Python surface passes a low bar; bugbear/isort/pyupgrade/simplify would catch real issues, and the dead `noqa`s show intent that was never enabled.
*Effort:* **S** (select `B,I,UP,SIM`; fix fallout incrementally).

---

### TIER 3 — SMALL TWEAKS (low-effort polish, noticeable payoff)

Each is **S** unless noted. Grouped by area.

**Compliance/CI cheap seams**
- **T1. SBOM in CI** — one step (`npm sbom --sbom-format cyclonedx` for frontend, `syft`/`cyclonedx-py` for the Python trees) satisfies seam #6.
- **T2. Document the FIPS posture** — record that crypto is FIPS-*capable* (via `cryptography`/OpenSSL) and that FIPS-*mode* is a deploy decision. No code change; closes the ambiguity.
- **T3. Promote the consumer-contract job** (`ci.yml:137-145`) to a required context once baked (13 tests currently advisory).
- **T4. Add coverage tooling** (pytest-cov + `@vitest/coverage-v8`), informational first. 1,100 tests of unknown reach is a blind spot; **S–M**.

**Deploy/runtime**
- **T5. Reconcile the `/healthz` body** (`main.py:48` `{"status":"ok"}`) with whatever the smoke config asserts — pick one contract. Pairs with M3.
- **T6. Code-split the frontend** — the build warns on a single 509 kB JS chunk (156 kB gzip); `react-markdown`/`remark-gfm` load even on the login screen. Route-level `React.lazy`; **S–M**.

**UX/a11y**
- **T7. Add dnd-kit `KeyboardSensor`** to Kanban + Grid (`Kanban.tsx:89-91`, `Grid.tsx:97-99`) — the flagship rank/move features are pointer-only; the keyboard sensor is near-free and makes them demoable without a mouse.
- **T8. cmdk arrow-nav `scrollIntoView`** (`CommandPalette.tsx:191-206`) — the highlighted row scrolls out of sight past ~a screenful of results.
- **T9. Surface SSE disconnect** (`useSSE.ts:89-92`) — the error handler is empty; a dropped stream silently stops updating the "live" board. Minimal: a reconnecting/offline pill.
- **T10. Restore focus indicators on `<select>`** (`Column.tsx:271`, `FilterBar.tsx:149` — `focus:outline-none` with no replacement) — keyboard users lose the focus ring.
- **T11. Stable list keys** — replace index-as-key in `Timeline.tsx:111`, `DependencyView.tsx:76`, `SubmitStory.tsx:365` (Timeline appends live SSE rows, so this is the fragile one).
- **T12. Replace `window.confirm`** in `ViewMenu.tsx:124` with the app's Radix dialog — it's the one spot that breaks the design language.
- **T13. Hide or finish Retros** (`routes/Retros.tsx:7-21`) — it's a "v1 coming soon" placeholder live in the nav; a tester clicking it hits a dead page.

**Hygiene**
- **T14. Remove the duplicated ~10 MB Gantry binaries** — `brand/gantry-motion-{1,2}.mp4` are byte-identical to `marketing/public/brand-media/gantry-motion-{1,2}.mp4`; plus `brand/gantry-logotype-{1,2}.png`. Biggest tracked files in the repo, all reverted-brand.
- **T15. Purge remaining "Gantry" strings** — 139+ occurrences across code/config beyond the marketing site (`frontend/src/lib/brand.ts:4` comment, `portalHandoff.ts`, `docker-compose.gantry.yml`, README/DECISIONS). Sweep once the brand direction is confirmed.
- **T16. Move/remove `engine/dashboard-v0/`** — a stray `index.html` + May handoff prototype loose in the engine package tree.
- **T17. Add mypy to `backend/`** — the auth-critical code (`cards_api/auth.py`) is the *least* type-checked in the repo (no mypy), and returns bare `dict` (`auth.py:35`, `main.py:53,61,70,84`). Add a `[tool.mypy]` section and type the dicts.
- **T18. Document the desktop-only limitation** — only 1 responsive breakpoint exists in all of `frontend/src`; the board forces ~1300 px and the header doesn't wrap. Fine for an internal tool *if testers are told*; otherwise **S–M** to make the header/board wrap.

---

### TIER 4 — POWERFUL FEATURES (bigger swings; roadmap-level, NOT required for alpha)

**P1. Make the FastAPI backend real: Postgres + full card CRUD + row-level security.**
Turn the auth shell into the actual multi-tenant Cards API — persistent storage, the ~20 routes the frontend needs, and RLS policies so isolation is enforced at the database, not in Python. This is the arc that retires the legacy Express backend for real *and* unlocks compliance seams #2 and #4 (encryption-at-rest and RLS get something to attach to). It's the single most valuable thing on the roadmap because it collapses the "two backends" problem and the persistence gap at once. **Effort: XL.**

**P2. Unify the two execution paths behind the runner.**
Today the board's submit-story shells out to the `claude` CLI via legacy Express (`stories/invoker.ts`), completely disconnected from the engine runner daemon — two execution engines, plus the two verifier trees (S6). Route submit-story through the runner so there's one execution engine, one verifier, one ledger. This is arguably *the product thesis* — the board as a real front-end to the runner — and today the halves only talk through the filesystem. **Effort: L.**

**P3. Local-GPU execution, end-to-end (finish KL3+).**
Provider-agnostic *tool-using* execution + per-card routing so "run this card on my 4090" does real edits and shell/git work, not just reasoning turns. This is a genuine differentiator (cost-free card execution when tokens run out) and the thing the whole KL1–KL5 seam was building toward. Builds on S7. **Effort: L–XL.**

**P4. Event-sourced, queryable audit/history store.**
Go beyond the cheap logger seam (S3) to an immutable, queryable event log of card and security events. It simultaneously powers the compliance audit trail, the card-event timeline the UI already renders, and future analytics — one substrate, three payoffs. **Effort: M–L.**

**P5. First-class monitoring wired into paradigm-ops.**
A structured `/healthz` readiness contract + committed `paradigm-status/v1` reports (S5) + eventually a richer endpoint the ops dashboard learns to poll, so the flagship becomes first-class in its own org's monitoring instead of invisible. **Effort: M.**

**P6. Live-collaboration hardening.**
SSE reconnect/offline resilience + a connection-status signal + optimistic-update reconciliation, turning the "live wallboard" from a silent-staleness risk into a trust feature for multi-user boards. Builds on T9. **Effort: S–M.**

**P7. Real accessibility pass.**
Keyboard DnD (T7), true ARIA menus, focus management, and measured color-contrast (the `#7d8694`-on-`#161b22` muted text and 9–10 px micro-type are inspection-flagged risks) — enough to pass a genuine a11y review and make the portfolio artifact defensible. **Effort: M.**

---

## 5. What's genuinely good (so the list isn't all deficit)

Truth-over-comfort cuts both ways; these are verified strengths, not padding:
- **The board UI is polished and consistent** — loading/empty/error states on nearly every async surface, not happy-path-only. Unusual for pre-alpha.
- **Test design is senior-level** — 1,100 passing, real thread-race and failure-path tests in the runner, RTL component tests on the frontend, an excellent JWKS/isolation suite.
- **Everything compiles and runs** — backend imports + starts + serves 401/200, frontend `tsc` + `vite build` clean, engine CLI works.
- **Frontend type hygiene is strong** — strict tsconfig, `tsc --noEmit` clean, zero `any` in non-test source.
- **Secrets handling is done right** — Infisical + env fallback, no committed secrets, gitleaks-conscious. The strongest compliance seam.
- **The auth/isolation code itself is high quality** — DI throughout, RS256-only allowlist, deliberate 401-vs-403 and 404-not-403 anti-probe handling, token `org_id` authoritative over request body.

The problem isn't the code that exists. It's the code that was declared done and isn't (the FastAPI CRUD/persistence), and the plumbing that was never built (deploy).

---

## 6. STATUS REPORT (house standard)

### 6.1 Current state
AgileCards is a well-built pre-alpha product with a **deployability gap, not a quality gap**. The working board and the engine runner are real, tested, and clean. The intended "new stack" (FastAPI backend) is a thin auth skeleton with no persistence and no CRUD; the actual product runs on the legacy Express backend the repo says to delete. There is no deploy artifact for the current stack, no single canonical deploy URL, and the app is invisible to the org's monitoring. Compliance seams are honored where they can be (secrets, FIPS-capable crypto) and absent where they depend on the missing datastore (audit log, encryption-at-rest, RLS). CI is green but two misconfigurations make "green" overstate protection.

**Repo hygiene note (not a product finding, but flag-worthy):** the primary working tree at `C:\dev\paradigm-agilecards` was left **dirty on a stale branch** (`fix/readme-paradigm-codes-claim-mod1`), with local `main` **8 commits behind** `origin/main` and leftover already-merged K11 files uncommitted in the tree. `vstart` would refuse this. This audit was run against `origin/main` (authoritative); the dirty tree is leftover cruft from the K11 session (#47), already on main — but it should be cleaned (the untracked `backend/cards_api/` + auth tests are duplicates of what's already merged). I did not touch it since it isn't mine to discard.

### 6.2 Blockers (hard, ordered)
1. **No deployable current stack** (M1 + M2) — the gating blocker; alpha literally cannot be reached by a tester.
2. **No canonical deploy target / wrong smoke gate** (M3).
3. **Marketing surface is deceptive + off-brand** (M4) *if* it's in alpha scope.

### 6.3 Decisions needed (Drew's calls — options + reasoning, not verdicts)

**Decision 1 — Which backend does alpha ship on?** *(This unblocks M1/M2 and sets the effort for half the list.)*
- **Option A — Ship legacy Express now (recommended for speed).** The board already works on it; it has SQLite persistence, path-traversal guards, and 96 tests. Reasoning: fastest path to a real, data-durable alpha (**M** to deploy). Cost: you ship the code marked "delete," the JWKS/org-isolation work from K11 is bypassed on the live path, and multi-tenant isolation reverts to Express's own model (not the audited Python one). Best if alpha is single-tenant or a small set of trusted testers.
- **Option B — Ship FastAPI.** Reasoning: it's the strategic target and carries the real auth/isolation story. Cost: it has no CRUD and no persistence — **XL** to reach parity, and testers' cards would evaporate on restart until persistence lands. Not viable for a near-term alpha.
- **Option C — Hybrid: keep legacy Express for CRUD behind the FastAPI JWKS gate (or a thin proxy).** Reasoning: preserves the working board *and* the audited auth. Cost: **L**, the most integration complexity, two backends to run.
- **My read:** Option A for a trusted-tester alpha now, with P1 (make FastAPI real) as the committed follow-through so "delete after K11" eventually becomes true. Relabel legacy from "frozen/delete" to "active until P1" so the landmine is defused in the meantime. State the multi-tenant limitation to testers explicitly.

**Decision 2 — Is the marketing landing page in alpha scope?**
- **Option A — Exclude it (recommended).** Reasoning: it's Gantry-branded and its form is non-functional; cutting the route out of alpha is **S** and removes M4 entirely. Testers go straight to the app.
- **Option B — Include and fix it.** Reasoning: needed if alpha has a public front door / signup funnel. Cost: **M** to re-brand to AgileCards + wire a real form.

**Decision 3 — Does alpha claim local-GPU execution?**
- If **yes**, S7/P3 (the tool-using local path) becomes near-blocking — **L–XL** — because today local models can't do real work. If **no**, it's roadmap and this whole strand defers cleanly. Reasoning: the value prop is real but the runtime isn't there; don't market what isn't wired.

### 6.4 Recommended next steps (sequenced)
1. **Make Decisions 1–3** (§6.3). Everything else forks on them; they're minutes of Drew's time against weeks of divergent work.
2. **Assuming Decision 1 = A:** relabel legacy as active (defuse the "delete" landmine — S), then build the deploy artifact for Express+Vite at one canonical URL (M2, **M**), fix the three-way URL/smoke contradiction (M3, **S**).
3. **Flip the two CI gates** (S1, **S**) — highest ROI, do it before the first tester touches the branch so alpha-period regressions can't merge green.
4. **Add the cheap seams that are painful to retrofit:** audit-log hook (S3), structured logging (S4), status report to paradigm-ops (S5) — all **S**, all worth doing before real traffic.
5. **Cheap credibility/polish pass:** canonical naming (S11), SubmitStory paths (S10), Retros dead-page (T13), duplicated-binary purge (T14). A day, big perceived-quality delta.
6. **Triage the 2–3 real mypy bugs** (S9) — they're plausible latent crashes in otherwise-strong runner code.
7. **Then** commit to the strategic arc: P1 (real FastAPI+Postgres+RLS) as the follow-through that retires the two-backend problem, with P2 (unify execution) close behind.

### 6.5 Bottom line
This is a strong product with a weak last mile. Nothing here says "the masterpiece is broken" — it says "the masterpiece was never wired up to a front door, and the docs claimed a migration that's 10% done." The fastest honest path to alpha is Option A + deploy plumbing + the two CI flips; the honest path to the *intended* product is P1. Don't let "CI green, 1,100 tests, K11 done" lull the decision — the tests are real, but they guard a store with no cards and a backend nothing deploys.

---

## 7. Method & limits (for auditability)

- **Isolation:** six detached worktrees off `origin/main` @ `7c9ce99` under `C:\dev\_worktrees\audit\`; agents were read/run-only (no git writes, no edits) to avoid the HEAD-corruption this repo has hit from shared checkouts.
- **Ran:** full `pytest`/`vitest` batteries (1,100 pass / 2 skip), `ruff`, `mypy` (strict, runner), `tsc --noEmit`, `npm run build` (backend + frontend + engine + legacy all install and build clean), and live `TestClient` probes of the FastAPI app.
- **Could not verify:** edge TLS termination (Cloudflare config is out of repo); the live Infisical fetch path (`infisical-python` not installed; exercised only in a prod image that doesn't exist); actual GitHub Actions run history (read from workflow definitions, not the API); git-history secret scan (working-tree grep only, found none); real coverage percentages (no coverage tooling in any suite); and any real end-to-end deploy (no artifact exists to deploy).
