#!/usr/bin/env bash
# docker-entrypoint-initdb.d bootstrap: the runtime login role.
#
# Migration 0001 creates `agilecards_app` as NOLOGIN if it does not exist --
# credentials never live in migrations. Something has to make the role a real
# login with a password before the API can connect; in this compose that
# something is this script, which the official postgres image runs exactly
# once, when the data volume is first initialized.
#
# Idempotent by construction (CREATE only when absent, ALTER always), so it is
# also safe to run by hand against an existing cluster.
#
# Security posture is deliberate and load-bearing (ADR-2026-07-16):
#   NOSUPERUSER + NOBYPASSRLS -- a superuser or BYPASSRLS connection silently
#   ignores row-level security, which would void the org-isolation guarantee.
#   No CREATEDB/CREATEROLE/INHERIT; table privileges are granted by the
#   migration (DML only, no DDL), not here.
set -euo pipefail

: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD must be set (see deploy/agilecards/.env.example)}"

# The password goes in as a psql variable and is quoted server-side with
# format(%L), so special characters cannot break out of the literal.
psql -v ON_ERROR_STOP=1 \
     -v app_password="${POSTGRES_APP_PASSWORD}" \
     --username "${POSTGRES_USER}" \
     --dbname "${POSTGRES_DB}" <<'EOSQL'
SELECT format(
    'CREATE ROLE agilecards_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS LOGIN PASSWORD %L',
    :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agilecards_app')
\gexec

SELECT format(
    'ALTER ROLE agilecards_app NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD %L',
    :'app_password')
\gexec
EOSQL

echo "initdb: role agilecards_app ensured (LOGIN, NOSUPERUSER, NOBYPASSRLS)"
