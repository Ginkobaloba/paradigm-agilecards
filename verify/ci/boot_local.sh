#!/usr/bin/env bash
# Boot THIS PR's backend AS IT WOULD DEPLOY, and smoke it over HTTP.
#
# WHY QUICK-VERIFY NEEDED THIS AT ALL. "Quick Verify (all PRs)" used to smoke a
# DEPLOYED url from verify/smoke.yml, and that url has been a placeholder for
# months: the board's old tunnel host stopped resolving and every current path
# answers only behind the portal auth wall, so nothing could be asserted
# unauthenticated from CI. The `detect` job therefore skipped quick-verify on
# every PR, so a context named "Quick Verify (all PRs)" reported "skipping" and
# verified nothing. That matters more now that verify-gate is a required context
# (ruleset 17880692) and quick-verify feeds it.
#
# WHY A NON-EDITABLE INSTALL, WITHOUT THE DEV EXTRAS. This is the part that gives
# the job value beyond the `backend (fastapi scaffold)` pytest job, and it was a
# correction to the first version of this script.
#
# `pip install -e "backend[dev]"` would put this boot in the SAME environment
# pytest runs in: the source tree on sys.path and every dev dependency present.
# In that environment a boot check is nearly redundant, and measurably so -- both
# defects injected while writing this (an endpoint that forgot its auth guard, and
# a broken `uvicorn app:app` entrypoint shim) were caught by the existing pytest
# suite as well.
#
# `pip install ./backend` builds and installs the WHEEL, with runtime deps only,
# and uvicorn runs from a directory where the source tree is NOT importable. That
# tests the ARTIFACT rather than the working copy, which pytest structurally
# cannot do, because pytest needs the dev extras and reads the source. Two classes
# of defect are therefore caught here and nowhere else, both verified by injecting
# them (see the PR body):
#
#   1. A runtime dependency declared in the dev extra instead of
#      [project.dependencies]. pytest installs dev extras, so it passes; the wheel
#      has no such dependency, so the boot dies on ImportError at startup.
#   2. A runtime module or data file left out of the package
#      ([tool.setuptools] packages / py-modules). Editable mode finds it on disk,
#      so pytest passes; the wheel omits it, so the boot cannot import it.
#
# Both ship a broken artifact while every test is green. There is no backend
# Dockerfile in this repo today, so booting the deploy image is not an available
# option; a wheel install run from outside the source tree is the closest thing
# to what would actually deploy.
#
# WHAT IT ASSERTS. None of these needs a real credential, which is what makes
# them runnable on every PR:
#   1. GET /healthz              -> 200. Public (backend/tests/test_endpoint_auth.py
#      calls it exactly that), so it proves the artifact booted and serves.
#   2. GET /api/me, NO header    -> 401/403 (AC-CARDS-006's bearer guard).
#   3. GET /api/cards, bad token -> 401/403. Verification rejects garbage rather
#      than trusting that a header was present. A 500 fails this smoke on purpose:
#      an unhandled exception on malformed input is a defect.
# Assertions 2 and 3 are ALSO covered in-process by
# backend/tests/test_endpoint_auth.py. They are kept because they are free once
# the artifact is up, not because they find something pytest misses. The value
# here is the artifact, not the assertions.
#
# NOT HERE, deliberately: the authenticated happy path. pytest already mints
# tokens against an in-process JWKS, so asserting /api/cards -> 200 over HTTP
# would be MORE duplicative than the three above, not less.
#
# NO DATABASE, deliberately. backend/cards_api reaches storage through
# CardStore/get_store and has no DATABASE_URL, psycopg, asyncpg or sqlalchemy
# reference on main: it is in-memory. The Postgres repository arrives with the
# Cards-API-Postgres work, and its service container belongs in that PR.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${BOOT_PORT:-8099}"
BASE="http://127.0.0.1:${PORT}"
LOG="${RUNNER_TEMP:-/tmp}/cards-api-boot.log"
READY_TIMEOUT="${READY_TIMEOUT:-60}"

pass=0
fail=0
check() { # name expected-pattern actual
  if printf '%s' "$3" | grep -qE "$2"; then
    echo "PASS  $1 (got $3)"; pass=$((pass + 1))
  else
    echo "FAIL  $1 (wanted $2, got $3)"; fail=$((fail + 1))
  fi
}

NEUTRAL_DIR=""
cleanup() {
  if [ -n "${APP_PID:-}" ] && kill -0 "$APP_PID" 2>/dev/null; then
    kill "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
  [ -n "$NEUTRAL_DIR" ] && rm -rf "$NEUTRAL_DIR"
}
trap cleanup EXIT

echo "== install the backend as a WHEEL, runtime deps only (not editable, no dev extras) =="
# Build from a CLEAN slate, or this check can pass on a STALE artifact, which is
# the exact failure it exists to prevent. Found while testing it: a leftover
# backend/build and *.egg-info from an earlier editable install made setuptools
# reuse the previous contents, so a wheel that should have omitted app.py still
# shipped it and the boot went green. A fresh CI checkout has neither directory,
# but pip's wheel cache can be restored by actions/cache and a developer running
# this locally will have both.
rm -rf "$REPO_ROOT/backend/build" "$REPO_ROOT/backend/dist" "$REPO_ROOT"/backend/*.egg-info
python -m pip install --quiet --upgrade pip
python -m pip install --quiet --no-cache-dir "$REPO_ROOT/backend" || {
  echo "ERROR: could not build or install backend/. Nothing to boot." >&2
  exit 1
}

echo "== boot uvicorn on :$PORT from OUTSIDE the source tree =="
# The working directory is load-bearing. Running from backend/ would put app.py
# and cards_api/ on sys.path via the cwd, so the SOURCE would shadow the
# installed wheel and a missing-from-package defect would be invisible. Boot from
# an empty directory so only what was installed can be imported.
NEUTRAL_DIR="$(mktemp -d)"
cd "$NEUTRAL_DIR"
# PARADIGM_JWKS_URL points at an RFC 2606 .invalid host on purpose: the settings
# default it to https://auth.paradigm.codes/.well-known/jwks.json, and this smoke
# must never depend on a real IdP being reachable, nor accidentally talk to
# production. Every assertion below is reached before a JWKS fetch is needed.
PARADIGM_JWKS_URL="https://jwks.invalid/.well-known/jwks.json" \
PARADIGM_JWT_ISSUER="https://auth.invalid" \
PARADIGM_JWT_AUDIENCE="paradigm-agilecards" \
  python -m uvicorn app:app --host 127.0.0.1 --port "$PORT" > "$LOG" 2>&1 &
APP_PID=$!

echo "== wait for readiness (timeout ${READY_TIMEOUT}s) =="
ready=""
for _ in $(seq 1 "$READY_TIMEOUT"); do
  # A dead process will never become ready, so stop the moment it exits instead
  # of burning the whole timeout on a crash.
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "ERROR: uvicorn exited before serving. Its log:" >&2
    sed 's/^/    /' "$LOG" >&2
    exit 1
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then ready=yes; break; fi
  sleep 1
done
if [ -z "$ready" ]; then
  echo "ERROR: /healthz did not answer 200 within ${READY_TIMEOUT}s. uvicorn log:" >&2
  sed 's/^/    /' "$LOG" >&2
  exit 1
fi
echo "ready"

echo
echo "== smoke =="
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz")"
check "/healthz is public and serves" '^200$' "$code"

code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/me")"
check "/api/me with NO Authorization is refused" '^(401|403)$' "$code"

code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer not-a-jwt' "$BASE/api/cards")"
check "/api/cards with a malformed bearer is refused" '^(401|403)$' "$code"

echo
echo "boot_local: $pass passed, $fail failed"
if [ "$fail" -ne 0 ]; then
  echo "uvicorn log follows, so a failure says why:" >&2
  sed 's/^/    /' "$LOG" >&2
  exit 1
fi
