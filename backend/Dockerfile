# Paradigm AgileCards API (FastAPI + Postgres). Runs as a non-root user; the
# database credentials arrive via env/Infisical at runtime (never baked in).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY pyproject.toml alembic.ini ./
COPY cards_api ./cards_api
COPY migrations ./migrations
COPY app.py ./

# .[infisical] so PARADIGM_SECRETS_PROVIDER=infisical works in production
# (env-var fallback needs nothing extra).
RUN pip install .[infisical]

RUN useradd --system --no-create-home cards
USER cards

EXPOSE 8000
# Single worker on purpose: the SSE bus is in-process (ADR D7). Scale up
# only after the bus moves to LISTEN/NOTIFY.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
