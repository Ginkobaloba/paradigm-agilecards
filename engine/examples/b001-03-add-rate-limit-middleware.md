---
verifier_schema_version: "1.3"
id: b001-03-add-rate-limit-middleware
title: Add rate-limit middleware to public API
project: C:\dev\project-example
status: backlog
points: 3
stakes: medium
difficulty: shallow
thinking_depth: shallow
model: claude-sonnet-4-6
extended_thinking: false
model_floor: sonnet
pin_required: false
requires_pre_approval: false
estimated_tokens: 18000
actual_tokens: null
estimated_duration_minutes: 25
actual_duration_minutes: null
sizing_note: "medium stakes (touches public surface, can be reverted) + shallow (well-trodden middleware pattern) -> tier 3, sonnet without thinking"
depends_on:
  - b001-02-pick-bucket-library
touches:
  - src/api/middleware/__init__.py
  - src/api/middleware/rate_limit.py
  - src/api/app.py
  - tests/api/middleware/test_rate_limit.py
expected_files:
  - src/api/middleware/**
  - src/api/app.py
  - tests/api/middleware/**
batch: b001
story_hash: 7c3a9b1e2d4f5a6b8c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b
trace_id: 0f8e7d6c-5b4a-3210-9876-543210fedcba
cost_cap_usd: null
created: 2026-05-16
started_at: null
finished_at: null
claimed_by: null
model_used: null
last_heartbeat: null
branch: card/b001-03-add-rate-limit-middleware
base_branch: main
merge_status: pending
cascade_history: []
verifier_cascade_history: []
standup_reason: null
---

## Context

The public API has no rate limiting today, so a single misbehaving client
can degrade latency for everyone else. The RFC card (b001-01) decided on
a token-bucket strategy keyed by API key, with per-tier limits. The
library-selection card (b001-02) picked `aiolimiter` for the in-process
case and documented when redis-backed buckets are needed. This card wires
the chosen library into the API middleware stack.

## Scope

- Add `src/api/middleware/rate_limit.py` implementing a starlette
  middleware that wraps each request in a token bucket lookup keyed by
  the `X-API-Key` header.
- Register the middleware in `src/api/app.py` in the order specified by
  the RFC (auth -> rate-limit -> tracing).
- Read per-tier limits from existing `config.api.rate_limits` keys.
  Defaults: free=60/min, pro=600/min, enterprise=6000/min.
- On overage, return HTTP 429 with `Retry-After` header set to the
  number of seconds until the bucket has room.
- Add unit tests in `tests/api/middleware/test_rate_limit.py` covering:
  under-limit pass-through, at-limit boundary, over-limit 429 with
  correct `Retry-After`, per-tier isolation.

## Out of scope

- Distributed (redis-backed) buckets. That's a follow-up card if traffic
  warrants it. The RFC explicitly said in-process is fine for v1.
- Metrics emission. Card b001-05 owns wiring rate-limit counters into
  the metrics middleware. Do not add metrics calls here; b001-05 will
  hook them in.
- Anything that changes `auth_middleware.py` or its order. The auth
  middleware is owned by b001-04 (in-flight in another branch). Touch
  conflicts here will surface in the manifest.
- Admin endpoints to inspect or reset buckets. Not in this batch.

## Acceptance criteria

All checks below are machine-verifiable. The executor runs each, records
pass/fail, and the card moves to `done/` only if every check passes.

```yaml
acceptance_criteria:
  - description: "Lint passes"
    type: command
    command: "make lint"
  - description: "Under-limit requests pass through unchanged"
    type: command
    command: "pytest tests/api/middleware/test_rate_limit.py::test_under_limit -q"
  - description: "At-limit boundary correct (Nth ok, N+1th 429)"
    type: command
    command: "pytest tests/api/middleware/test_rate_limit.py::test_at_limit_boundary -q"
  - description: "429 carries a valid Retry-After header"
    type: command
    command: "pytest tests/api/middleware/test_rate_limit.py::test_retry_after_header -q"
  - description: "Per-key isolation"
    type: command
    command: "pytest tests/api/middleware/test_rate_limit.py::test_per_key_isolation -q"
  - description: "Middleware file created"
    type: file_exists
    path: "src/api/middleware/rate_limit.py"
  - description: "Middleware registered in app entry point"
    type: file_contains
    path: "src/api/app.py"
    pattern: "rate_limit"
  - description: "Full api test suite passes"
    type: command
    command: "make test-api"
    timeout_sec: 300
```

## Pointers

- RFC: `docs/rfcs/rate-limiting.md` (produced by card b001-01)
- Library selection notes: `docs/decisions/2026-05-rate-limit-library.md`
  (produced by card b001-02)
- Originating user-story fragment: "As a service operator I want to
  rate-limit the public API so a single misbehaving client can't degrade
  the experience for everyone."
- Related cards: b001-04 (auth middleware ordering), b001-05 (rate-limit
  metrics)
- Existing middleware patterns to mirror: `src/api/middleware/tracing.py`
