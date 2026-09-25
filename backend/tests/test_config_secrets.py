"""AC-CARDS-008 (amended 2026-09-19) -- settings from env vars; no .env secrets.

These tests lock the boot-time config behavior: safe public defaults, env
override for local/CI, env as the only secret source, and a loud failure when a
deploy still asks for the removed Infisical provider. The "no real secrets in
the tree / gitleaks-clean" half of the AC is an audit (see the verification
record); the repo-hygiene part is asserted here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_env_provider_reads_os_environ(monkeypatch) -> None:
    monkeypatch.setenv("PARADIGM_SECRETS_PROVIDER", "env")
    monkeypatch.setenv("PARADIGM_JWT_AUDIENCE", "aud-from-env")
    s = load_settings()
    assert isinstance(s, Settings)
    assert s.jwt_audience == "aud-from-env"


def test_unset_provider_defaults_to_os_environ(monkeypatch) -> None:
    monkeypatch.delenv("PARADIGM_SECRETS_PROVIDER", raising=False)
    monkeypatch.setenv("PARADIGM_JWT_ISSUER", "https://idp.from-env")
    s = load_settings()
    assert s.jwt_issuer == "https://idp.from-env"
    assert s.jwks_url == "https://idp.from-env/.well-known/jwks.json"


@pytest.mark.parametrize("value", ["", "  ", "ENV", " Env "])
def test_env_provider_tolerates_blank_and_case(monkeypatch, value: str) -> None:
    monkeypatch.setenv("PARADIGM_SECRETS_PROVIDER", value)
    monkeypatch.setenv("PARADIGM_JWT_AUDIENCE", "aud-from-env")
    assert load_settings().jwt_audience == "aud-from-env"


@pytest.mark.parametrize("value", ["infisical", "Infisical", " INFISICAL "])
def test_removed_infisical_provider_fails_loudly(monkeypatch, value: str) -> None:
    # A deploy still configured for Infisical must not boot on env vars or
    # defaults by accident: the vault is gone, so fail with a fix-it message.
    monkeypatch.setenv("PARADIGM_SECRETS_PROVIDER", value)
    monkeypatch.setenv("PARADIGM_JWT_AUDIENCE", "would-be-silently-used")
    with pytest.raises(RuntimeError, match="no longer supported") as exc:
        load_settings()
    assert "environment variables" in str(exc.value)


def test_unknown_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("PARADIGM_SECRETS_PROVIDER", "vault")
    with pytest.raises(ValueError, match="Unknown PARADIGM_SECRETS_PROVIDER"):
        load_settings()


def test_explicit_source_bypasses_provider_selection(monkeypatch) -> None:
    # Tests (and any caller) passing a mapping never consult the provider.
    monkeypatch.setenv("PARADIGM_SECRETS_PROVIDER", "infisical")
    s = load_settings(source={"PARADIGM_JWT_AUDIENCE": "explicit"})
    assert s.jwt_audience == "explicit"


def test_infisical_code_is_gone() -> None:
    assert not hasattr(config, "load_from_infisical")
    assert "infisical_client" not in Path(config.__file__).read_text(encoding="utf-8")


def test_no_committed_dotenv_secret_file() -> None:
    # A real .env (as opposed to .env.example) must never be committed.
    assert not (_BACKEND_ROOT / ".env").exists(), "backend/.env must not be committed"
