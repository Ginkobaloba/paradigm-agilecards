# tests/ (repo-level)

Cross-cutting integration and end-to-end tests for paradigm-agilecards live
here. This directory is part of the target repo shape required by
**AC-CARDS-002** (the repo must contain `backend/`, `frontend/`, `tests/`,
`docs/`, and CI config).

It is a **placeholder** as of chunk K2. It is intentionally not wired into CI
yet -- there is nothing cross-cutting to run until later chunks land.

## Where tests live today

- **Backend (FastAPI) unit tests:** [`../backend/tests`](../backend/tests)
  (run in CI by the `backend (fastapi scaffold)` job).
- **Frontend (Boards UI) tests:** [`../frontend/src`](../frontend/src)
  (Vitest, run in CI by the `board frontend battery (lint + vitest)` job).
- **Legacy Express backend tests:** [`../legacy/board-express/backend/src`](../legacy/board-express/backend/src)
  (run in CI by the `board backend battery (build + tests)` job; reference
  only -- K11 replaces this backend).

## What lands here later

- **K16** -- consumer contract tests against `@paradigm/auth` and
  `@paradigm/llm-client` (AC-CARDS-013/014).
- **V** -- the v1 end-to-end workflow test (AC-CARDS-011), though the FPS
  currently homes that under `verification/`.
