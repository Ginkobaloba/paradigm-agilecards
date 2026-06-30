---
AC: AC-CARDS-002
Phase: v1
Status: PASS
Verifier: Claude (K2)
Verified at: 2026-06-30
Evidence: >
  AC text: "Repo structure includes backend/ (Python or C#), frontend/
  (Boards UI), tests/, docs/, and CI config. Verification: Audit -- ls check."

  ls check (branch head, squash-merged verbatim to main via PR #44):
  - backend/  -> FastAPI scaffold: app.py, pyproject.toml, README.md, tests/. Python.
  - frontend/ -> React/Vite Boards UI (moved from apps/board/frontend).
  - tests/    -> repo-level integration/e2e placeholder (README.md).
  - docs/     -> adr/, handoffs/, board/.
  - .github/workflows/ci.yml -> CI config present (4 jobs).

  Audit command output (all present):
    OK backend  OK frontend  OK tests  OK docs  OK .github/workflows/ci.yml
    OK backend/app.py  OK backend/pyproject.toml
  CI run 28424040046: engine runner battery, board frontend battery, board
  backend battery, backend (fastapi scaffold) -- all pass.
---

# AC-CARDS-002 -- Repo structure: backend/ frontend/ tests/ docs/ + CI

## Audit steps

```bash
ls -d backend frontend tests docs .github/workflows/ci.yml
ls backend/app.py backend/pyproject.toml
```

Expected: all paths present. `backend/` is Python (FastAPI), `frontend/` is the
Boards UI, `tests/` and `docs/` exist, and CI config is present.

## Result

PASS -- all required structure present; backend is Python/FastAPI; CI green.
