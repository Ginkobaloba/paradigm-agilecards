#!/bin/sh
# First-boot role/database provisioning (runs inside the postgres image).
# cards_owner: owns the schema, runs migrations, CREATEROLE because the
#   initial migration ensures cards_app exists.
# cards_app: what the API connects as — LOGIN only, no superuser, NOBYPASSRLS,
#   never the table owner. RLS policies bind to it (ADR D3).
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE ROLE cards_owner LOGIN CREATEROLE PASSWORD '${CARDS_OWNER_PASSWORD}';
    CREATE ROLE cards_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '${CARDS_APP_PASSWORD}';
    CREATE DATABASE cards OWNER cards_owner;
EOSQL
