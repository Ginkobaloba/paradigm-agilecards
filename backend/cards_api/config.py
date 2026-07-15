"""Boot-time configuration and secret loading (AC-CARDS-008).

Secrets are pulled from Infisical at boot via ``infisical-python`` when the
service is configured for it (production / staging). There are NO real secrets
committed to this repo: ``.env.example`` ships placeholders only, and the
Infisical machine-identity credentials themselves are injected by the runtime
environment, never stored in the tree (gitleaks-clean).

Local development and CI fall back to plain environment variables so the app
boots without an Infisical dependency. The JWT issuer/audience/JWKS values are
public configuration (not secrets); they carry safe defaults pointing at the
Paradigm IdP so the app is importable with zero configuration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Public, non-secret defaults. The IdP issuer and this service's audience are
# not sensitive; keeping them as defaults means the app imports cleanly in CI.
_DEFAULT_ISSUER = "https://auth.paradigm.codes"
_DEFAULT_AUDIENCE = "paradigm-agilecards"


@dataclass(frozen=True)
class Settings:
    jwt_issuer: str
    jwt_audience: str
    jwks_url: str


def load_settings(source: Mapping[str, str] | None = None) -> Settings:
    """Resolve settings at boot.

    Args:
        source: an explicit secret mapping (used by tests). When omitted, the
            secret source is chosen by ``PARADIGM_SECRETS_PROVIDER``: ``infisical``
            fetches from Infisical, anything else reads ``os.environ``.
    """
    secrets = source if source is not None else _load_secret_source()
    issuer = secrets.get("PARADIGM_JWT_ISSUER", _DEFAULT_ISSUER)
    audience = secrets.get("PARADIGM_JWT_AUDIENCE", _DEFAULT_AUDIENCE)
    jwks_url = secrets.get("PARADIGM_JWKS_URL") or f"{issuer}/.well-known/jwks.json"
    return Settings(jwt_issuer=issuer, jwt_audience=audience, jwks_url=jwks_url)


def _load_secret_source() -> Mapping[str, str]:
    provider = os.environ.get("PARADIGM_SECRETS_PROVIDER", "env").strip().lower()
    if provider == "infisical":
        return load_from_infisical()
    return os.environ


def load_from_infisical() -> Mapping[str, str]:
    """Fetch this service's secrets from Infisical at boot.

    Authenticates with a Universal Auth machine identity (client id/secret), so
    no human token and no secret material lives in the repo -- the identity
    credentials are injected by the runtime. Returns a plain dict of
    secret-name -> value.

    Uses the ``infisical-python`` SDK (import ``infisical_client``). It is an
    optional import, required only when the service is actually configured for
    Infisical, so CI and local dev never need it installed. This path is
    exercised in the deploy chunk's smoke test, not in unit CI (see the
    AC-CARDS-008 verification record).
    """
    try:
        from infisical_client import (
            AuthenticationOptions,
            ClientSettings,
            InfisicalClient,
            ListSecretsOptions,
            UniversalAuthMethod,
        )
    except ImportError as exc:  # pragma: no cover - exercised only in prod images
        raise RuntimeError(
            "PARADIGM_SECRETS_PROVIDER=infisical but the 'infisical-python' "
            "package is not installed. Add it to the production image."
        ) from exc

    site_url = os.environ.get("INFISICAL_API_URL", "https://app.infisical.com")
    client = InfisicalClient(
        ClientSettings(
            site_url=site_url,
            auth=AuthenticationOptions(
                universal_auth=UniversalAuthMethod(
                    client_id=os.environ["INFISICAL_CLIENT_ID"],
                    client_secret=os.environ["INFISICAL_CLIENT_SECRET"],
                )
            ),
        )
    )
    secrets = client.listSecrets(
        options=ListSecretsOptions(
            project_id=os.environ["INFISICAL_PROJECT_ID"],
            environment=os.environ.get("INFISICAL_ENVIRONMENT", "prod"),
            path=os.environ.get("INFISICAL_SECRET_PATH", "/"),
        )
    )
    return {s.secret_key: s.secret_value for s in secrets}
