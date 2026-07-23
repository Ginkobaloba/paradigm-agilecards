# syntax=docker/dockerfile:1
# Paradigm AgileCards frontend -- static build served by Caddy.
#
# Stage 1 builds the Vite bundle; stage 2 is a plain Caddy serving the dist
# plus reverse-proxying /api, /events and the health probes to the backend
# service (routing lives in deploy/agilecards/Caddyfile, copied in here so
# the image is self-contained).
#
# Build context is the REPO ROOT, not frontend/ -- the image needs both
# frontend/ and deploy/agilecards/Caddyfile:
#   docker build -t agilecards-web -f frontend/Dockerfile .

# ---------------------------------------------------------------- build -----
FROM node:20-alpine AS build

WORKDIR /build

# Lockfile-first copy keeps the npm ci layer cached across source-only edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# The only build-time knob the app reads (vite.config.ts): where the app is
# mounted in URL space. "/" for standalone hosting; "/board/" behind the
# Paradigm portal reverse proxy. API calls are relative, so no VITE_API_URL
# exists -- the Caddyfile proxies /api same-origin.
ARG VITE_BASE_PATH=/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}

# `npm run build` = tsc --noEmit + vite build: the image build fails on type
# errors, the same gate CI applies.
RUN npm run build

# ---------------------------------------------------------------- serve -----
FROM caddy:2-alpine

# Single source of truth for routing + security headers. The same file also
# documents the direct-HTTPS posture (see the commented site block inside).
COPY deploy/agilecards/Caddyfile /etc/caddy/Caddyfile

COPY --from=build /build/dist /srv

EXPOSE 80
# caddy:2-alpine's default CMD (caddy run --config /etc/caddy/Caddyfile) is
# exactly what we want; no override needed.
