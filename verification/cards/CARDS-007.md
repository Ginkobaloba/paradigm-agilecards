---
AC: AC-CARDS-007
Phase: v1
Status: PASS
Verifier: Claude (K11)
Verified at: 2026-06-30
Evidence: >
  AC text: extract org_id + roles from verified claims, enforce authorization;
  multi-org isolation test.

  Implementation:
  - backend/cards_api/auth.py -- ParadigmClaims carries sub, org_id, roles
    extracted from the verified token; missing org_id is rejected at verify time.
  - backend/cards_api/store.py -- CardStore is org-scoped at the boundary:
    list_for_org() and get_for_org() filter by org_id; get_for_org() returns
    None for a foreign card so the route answers 404 (not 403), preventing
    cross-org id probing.
  - backend/cards_api/deps.py -- require_roles(*roles) is a dependency factory
    enforcing role-based authorization (403 on insufficient role).
  - backend/cards_api/main.py -- GET /api/cards and /api/cards/{id} are scoped
    to claims.org_id; POST /api/cards requires the "admin" role and writes with
    org_id taken from the TOKEN, never the request body.

  Tests (backend/tests/test_org_isolation.py, 8 cases, all PASS):
    claims extracted (sub/org_id/roles via /api/me); org A sees only {a1,a2};
    org B sees only {b1}; cross-org GET /api/cards/a1 as org B -> 404; same-org
    read -> 200; member POST -> 403; admin POST -> 201 and visible only to its
    own org; body-injected foreign org_id is ignored (token org_id wins).

  Command (worktree feat/cards-k11-jwt-auth off origin/main c87bb88):
    pytest -> 32 passed, 1 warning in 0.92s
---

# AC-CARDS-007 -- org_id + roles from claims; authorization + isolation

## Extraction

`org_id` and `roles` are read from the **verified** token only (never from query
or body). `GET /api/me` echoes `{sub, org_id, roles}` straight from
`ParadigmClaims`. A token without `org_id` fails verification (`missing_org_id`).

## Isolation (multi-org)

Isolation is enforced at the store boundary, not just the route:

- `GET /api/cards` returns only the caller-org's cards.
- `GET /api/cards/{id}` for another org's card returns **404** (not 403), so one
  org cannot probe another org's card ids.
- `POST /api/cards` writes the new card with `org_id` from the token; a foreign
  `org_id` injected in the body is ignored.

Two orgs (`org_acme`, `org_globex`) with disjoint seed cards prove the boundary
in both directions.

## Authorization (roles)

`require_roles("admin")` gates the mutating route: a `member`-only token gets
403; an `admin` token gets 201.

## Audit steps

```bash
cd backend
pytest tests/test_org_isolation.py -q
```

## Result

PASS -- claims extracted from the verified token; cross-org reads/writes are
isolated; role-based authorization enforced on mutation.
