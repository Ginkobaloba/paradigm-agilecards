# frontend/contracts/ -- consumer contract tests (CARDS-014, chunk K16)

Contract tests that pin the frozen `@paradigm/llm-client` v1 surface the Boards
Node BFF depends on. Run by the dedicated **`contracts`** CI job on every PR and
every Renovate bump of a `@paradigm/*` package.

## Status: scaffold (live suite wired by K11b)

`@paradigm/llm-client` is TypeScript-only and consumed **server-side** by the
Node BFF (the browser never holds a provider key). The BFF lands with chunk
**K11b**, so:

- `llm-client.test.ts` runs **now** with ACTIVE tests that pin the contract --
  the exactly-five-method interface (`complete` / `stream` / `embed` /
  `onTelemetry` / `registerAdapter`), the 9-field telemetry payload, the
  provider-selection rule, and the structured error codes -- mirrored from
  `@paradigm/llm-client@0.1.0`.
- A `describe.skip(...)` LIVE block holds the conformance suite written against
  the real package. It is dormant only because the package is not a dependency
  yet.

### Wiring (K11b)

1. Add `@paradigm/llm-client` to `frontend/package.json` (GitHub Packages scope,
   configured in `.npmrc`).
2. Change `describe.skip(` to `describe(` on the LIVE block.
3. Replace the local contract mirror with
   `import type { ... } from "@paradigm/llm-client"` if desired.

If the frozen surface drifted, the suite fails -- which is the point. The
interface is frozen at v1.0.0; any break is a major bump and Tier-3 (Drew)
approval (`AC-LLM-003`).

## Run locally

```powershell
cd C:\dev\paradigm-agilecards\frontend
npm ci
npm run test:contracts
```

The Python counterpart (`@paradigm/auth`, **CARDS-013**) lives at
[`../../backend/contracts/`](../../backend/contracts/).
