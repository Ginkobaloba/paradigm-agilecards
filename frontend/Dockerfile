# Boards UI: Vite static build served by Caddy, which also fronts the API
# (same-origin /api + /events + /healthz — see frontend/src/lib/baseUrl.ts).
FROM node:20-slim AS build

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG VITE_BASE_PATH=/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
RUN npm run build

FROM caddy:2-alpine

COPY --from=build /build/dist /srv
# The Caddyfile is mounted by compose (deploy/Caddyfile) so proxy targets
# stay configuration, not image content.
