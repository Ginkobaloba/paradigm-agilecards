# Deep-verify report -- backend/cards_api (drop Infisical provider) -- 2026-09-19

Verified-Commit: 09284096572b3706404ee4fcfdd29154f11630e8
Branch: fix/drop-infisical-provider
PR: #70
Operator: Claude (session dev-44), for Drew
Environment: Windows 11 (DREWSPC), Python 3.13 venv with `pip install -e backend[dev]` (no `infisical-python`); real `uvicorn app:app` subprocesses on 127.0.0.1; throwaway RSA key + local JWKS server; no network beyond loopback.

## Layers run

| layer | scope | result |
|---|---|---|
| 1-4 (network/headless) | backend pytest 42/42; `ruff check` (0.15.22) clean; real server boot + HTTP auth round trips under each `PARADIGM_SECRETS_PROVIDER` setting (harness below, 15 checks) | PASS |
| 5 (headed, computer-use) | **Not applicable.** The change is backend boot configuration only; no UI surface or board flow changed and the backend is not deployed anywhere to click through. | N/A |
| 6 (adversarial) | alg=`none` token; tokens minted for the built-in default issuer/audience; expired token; missing `org_id`; provider values `infisical` / ` INFISICAL ` / `vault` / ` ENV ` | PASS |

## Evidence

Harness: `verify/reports/DEEP_2026-09-19_drop-infisical.harness.py` (run from `backend/` with the backend venv's python; exits 0 only when every check passes). Final run, 15/15:

- A1-A2: provider unset, server boots, `/healthz` 200.
- A3: `/api/me` without a token -> 401.
- A4: RS256 token for the env-configured issuer + audience -> 200, claims echoed (`org_id=org-a`).
- **A5/A6: a token minted for the built-in DEFAULT audience or issuer -> 401.** This proves the env values, not the defaults, drive live verification.
- A7-A9: expired -> 401; alg=`none` -> 401; empty `org_id` -> 401.
- B, B': `PARADIGM_SECRETS_PROVIDER=infisical` (and ` INFISICAL `): process exits rc=1 with the "no longer supported" fix-it message. It does not boot.
- C: `=vault`: exits rc=1 with "Unknown PARADIGM_SECRETS_PROVIDER".
- D1-D2: `= ENV `: boots and serves auth from env values.
- E1: `infisical_client` is not installed, and the app imports without it.

## Notes / follow-ups

- The first harness run reported B/B' as FAIL ("booted"). Cause: the harness, not the code. It judged boot by a successful TCP connect, which can hit an unrelated listener. Running `uvicorn app:app` directly with `infisical` exits 1 with the message. The harness now judges fail-closed scenarios by process exit + stderr, and no stray server processes were left behind. Recorded here rather than dropped.
- Behavior change to note at merge: an unknown `PARADIGM_SECRETS_PROVIDER` value now raises instead of silently reading `os.environ`. No running container or deploy config sets the variable (checked 2026-09-19).
- `ruff format --check` flags `contracts/test_paradigm_auth_contract.py`, which is untouched by this PR and not format-checked in CI.

Overall: PASS