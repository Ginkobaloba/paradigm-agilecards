---
AC: AC-CARDS-014
Phase: v1
Status: PASS
Verifier: Claude (K16)
Verified at: 2026-06-30
Evidence: >
  AC text (from the K-DOC README seed / roadmap K16): "Consumer contract tests
  in paradigm-agilecards against @paradigm/llm-client -- interface conformance,
  telemetry hook (9-field payload), provider selection, error shape. Scaffold for
  K11b to wire when the Node BFF lands."

  Scope note: @paradigm/llm-client is TypeScript-only and consumed server-side by
  the Node BFF, which does not exist yet (chunk K11b). Per the chunk, CARDS-014 is
  a SCAFFOLD. It is delivered as a real, green vitest suite that PINS the frozen
  contract now, plus a dormant LIVE conformance block wired by K11b.

  Deliverable:
  - frontend/contracts/llm-client.test.ts -- contract mirrored from the real
    @paradigm/llm-client@0.1.0 source
    (paradigm-platform/packages/llm-client/src/{types,errors}.ts), NOT guessed:
      * interface conformance: exactly five frozen methods -- complete / stream /
        embed / onTelemetry / registerAdapter (AC-LLM-001/003); no Cards/tenant
        methods on the surface
      * telemetry hook: 9-field payload = 8 required (provider, model, request_id,
        prompt_tokens, completion_tokens, latency_ms, cost_estimate_usd, success)
        + optional error_code (AC-LLM-012); success event = 8 keys, failure = 9
      * provider selection: request.provider, else client default, else the stable
        no_provider_selected error (AC-LLM-008)
      * error shape: stable codes (unknown_provider, no_provider_selected,
        missing_api_key, unsupported_operation, invalid_request, provider_http_error)
  - frontend/vitest.contracts.config.ts + package.json `test:contracts` script
    (node env, scoped to contracts/, independent of the src/ Boards UI suite).
  - .github/workflows/ci.yml `contracts` job runs `npm run test:contracts` on
    every PR (and thus every Renovate @paradigm/* bump).

  The LIVE conformance block (instantiate createClient with a mock fetch, assert
  the telemetry payload + NoProviderSelectedError against the REAL package) is
  `describe.skip(...)` and uses a non-static dynamic import, so the file loads
  green while @paradigm/llm-client is not yet a dependency. K11b activates it:
  add the package, flip `describe.skip` -> `describe`. See frontend/contracts/README.md.

  Local run (2026-06-30):
    cd frontend
    npm run test:contracts
    -> Test Files 1 passed (1); Tests 4 passed | 2 skipped (6)
---

# AC-CARDS-014 -- TS consumer contract test against `@paradigm/llm-client` (scaffold)

## Audit steps

```powershell
cd C:\dev\paradigm-agilecards\frontend
npm ci
npm run test:contracts
```

Expected: the active contract checks pass; the live conformance block is skipped
(it activates in K11b when the BFF and the package land). CI runs the same in the
`contracts` job on every PR and every Renovate `@paradigm/*` bump.

## Result

PASS (scaffold) -- 4 active contract checks pass, 2 live conformance tests
scaffolded and intentionally dormant pending K11b. The contract is pinned from
the real `@paradigm/llm-client@0.1.0` source (interface, 9-field telemetry,
provider selection, error codes), so a future drift fails the suite. This matches
the chunk's stated deliverable ("Scaffold for K11b to wire when BFF lands"); the
live-package conformance is verified in K11b, not here.
