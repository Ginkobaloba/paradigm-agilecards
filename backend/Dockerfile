# syntax=docker/dockerfile:1
# Paradigm AgileCards backend (FastAPI) -- production image.
#
# Multi-stage: the builder stage owns pip and the build toolchain; the final
# stage carries only the installed virtualenv, the Alembic migration tree, and
# a non-root user. Same image serves two compose roles:
#   - api:     CMD (uvicorn) as-is, connects as the RLS-bound agilecards_app role
#   - migrate: command overridden to `alembic upgrade head`, connects as owner
# Build from the backend/ directory:  docker build -t agilecards-api backend

# ---------------------------------------------------------------- builder ---
FROM python:3.11-slim AS builder

# A dedicated venv keeps the runtime interpreter's site-packages pristine and
# makes the "copy exactly what was installed" handoff to the final stage exact.
ENV VIRTUAL_ENV=/opt/venv \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /src

# The project is installed non-editable, so cards_api and the app.py shim land
# in site-packages -- the final stage needs no source checkout at all.
# `.[infisical]` pulls the Infisical SDK: production sources secrets at boot
# (AC-CARDS-008); dev/CI use the env-var fallback and skip the extra.
COPY pyproject.toml app.py ./
COPY cards_api ./cards_api
RUN pip install ".[infisical]"

# ------------------------------------------------------------------ final ---
FROM python:3.11-slim

# Fail fast and log immediately: no .pyc litter, unbuffered stdout/stderr so
# container logs stream in real time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root runtime user. Fixed UID/GID so volume permissions are predictable
# across rebuilds; no shell, no home-directory writes needed.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

# The migration tree ships in the image so the compose `migrate` service can
# run `alembic upgrade head` from the same artifact the API runs from -- one
# image, one version, no schema drift between what migrated and what serves.
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations

USER app
EXPOSE 8000

# Liveness probe against /healthz. python -c because the slim base ships no
# curl/wget and installing one would only widen the attack surface.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
