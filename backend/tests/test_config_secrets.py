"""AC-CARDS-008 -- secrets sourced from Infisical at boot; no .env secrets.

These tests lock the boot-time config behavior: safe public defaults, env
override for local/CI, and the Infisical provider path. The "no real secrets in
the tree / gitleaks-clean" half of the AC is an audit (see the verification
record); the repo-hygiene part is asserted here too.
"""

from __future__ import annotations

from pathlib import Path

import cards_api.config as config
from cards_api.config import Settings, load_settings

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_defaults_are_public_non_secrets() -> None:
    s = load_settings(source={})
    assert s.jwt_issuer == "https://auth.paradigm.codes"
    assert s.jwt_audience == "paradigm-agilecards"
    assert s.jwks_url == "https://auth.paradigm.codes/.well-known/jwks.json"


def test_env_values_override_defaults() -> None:
    s = load_settings(
        source={
            "PARADIGM_JWT_ISSUER": "https://auth.example.test",
            "PARADIGM_JWT_AUDIENCE": "some-service",
        }
    )
    assert s.jwt_issuer == "https://auth.example.test"
    assert s.jwt_audience == "some-service"
    assert s.jwks_url == "https://auth.example.test/.well-known/jwks.json"


def test_explicit_jwks_url_wins() -> None:
    s = load_settings(source={"PARADIGM_JWKS_URL": "https://cdn.example.test/keys.json"})
    assert s.jwks_url == "https://cdn.example.test/keys.json"


def test_infisical_provider_pulls_from_infisical_at_boot(monkeypatch) -> None:
    # Selecting the infisical provider must route secret loading through the
    # Infisical client, not os.environ.
    monkeypatch.setenv("PARADIGM_SECRETS_PROVIDER", "infisical")
    captured = {
        "PARADIGM_JWT_ISSUER": "https://idp.from-infisical",
        "PARADIGM_JWT_AUDIENCE": "aud-from-infisical",
    }
    monkeypatch.setattr(config, "load_from_infisical", lambda: captured)

    s = load_settings()
    assert isinstance(s, Settings)
    assert s.jwt_issuer == "https://idp.from-infisical"
    assert s.jwt_audience == "aud-from-infisical"


def test_env_provider_reads_os_environ(monkeypatch) -> None:
    monkeypatch.setenv("PARADIGM_SECRETS_PROVIDER", "env")
    monkeypatch.setenv("PARADIGM_JWT_AUDIENCE", "aud-from-env")
    s = load_settings()
    assert s.jwt_audience == "aud-from-env"


def test_no_committed_dotenv_secret_file() -> None:
    # A real .env (as opposed to .env.example) must never be committed.
    assert not (_BACKEND_ROOT / ".env").exists(), "backend/.env must not be committed"
