"""Boot-time configuration (AC-CARDS-008, amended 2026-09-19).

Settings come from environment variables, injected by the runtime. There are NO
real secrets committed to this repo: ``.env.example`` ships placeholders only
(gitleaks-clean).

The Infisical provider was removed on 2026-09-19. Paradigm's Infisical vault is
not running (its database was lost on 2026-07-29 and never restored), so the
``infisical`` path could only fail at boot. Setting
``PARADIGM_SECRETS_PROVIDER=infisical`` now raises a clear error instead of
quietly booting on env vars or defaults, so a deploy that still expects Infisical
fails loudly rather than running with the wrong configuration.

The JWT issuer/audience/JWKS values are public configuration (not secrets); they
carry safe defaults pointing at the Paradigm IdP so the app is importable with
zero configuration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Public, non-secret defaults. The IdP issuer and this service's audience are
# not sensitive; keeping them as defaults means the app imports cleanly in CI.
_DEFAULT_ISSUER = "https://auth.paradigm.codes"
_DEFAULT_AUDIENCE = "paradigm-agilecards"

# Only one secret source remains. Unset or "env" means os.environ.
_ENV_PROVIDER = "env"


@dataclass(frozen=True)
class Settings:
    jwt_issuer: str
    jwt_audience: str
    jwks_url: str
    # Postgres URL for the cards_app runtime role (NOBYPASSRLS, non-owner —
    # see docs/adr/ADR-2026-07-16-cards-api-postgres-rls.md). Contains a
    # credential, so it is sourced from the secret provider like everything
    # else. None means "no database configured" (the app still boots and
    # reports it via /healthz; data routes answer 503).
    database_url: str | None = None


def load_settings(source: Mapping[str, str] | None = None) -> Settings:
    """Resolve settings at boot.

    Args:
        source: an explicit mapping (used by tests). When omitted, settings are
            read from ``os.environ`` (see ``_load_secret_source``).
    """
    secrets = source if source is not None else _load_secret_source()
    issuer = secrets.get("PARADIGM_JWT_ISSUER", _DEFAULT_ISSUER)
    audience = secrets.get("PARADIGM_JWT_AUDIENCE", _DEFAULT_AUDIENCE)
    jwks_url = secrets.get("PARADIGM_JWKS_URL") or f"{issuer}/.well-known/jwks.json"
    return Settings(
        jwt_issuer=issuer,
        jwt_audience=audience,
        jwks_url=jwks_url,
        database_url=secrets.get("CARDS_DATABASE_URL") or None,
    )


def _load_secret_source() -> Mapping[str, str]:
    provider = os.environ.get("PARADIGM_SECRETS_PROVIDER", _ENV_PROVIDER).strip().lower()
    if provider in ("", _ENV_PROVIDER):
        return os.environ
    if provider == "infisical":
        raise RuntimeError(
            "PARADIGM_SECRETS_PROVIDER=infisical is no longer supported: the "
            "Infisical provider was removed on 2026-09-19 (the vault is not "
            "running). Inject PARADIGM_JWT_ISSUER / PARADIGM_JWT_AUDIENCE / "
            "PARADIGM_JWKS_URL as environment variables and unset "
            "PARADIGM_SECRETS_PROVIDER (or set it to 'env')."
        )
    raise ValueError(
        f"Unknown PARADIGM_SECRETS_PROVIDER={provider!r}; the only supported "
        "value is 'env' (or leave it unset)."
    )
