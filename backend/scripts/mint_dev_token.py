"""Mint a DEV-ONLY RS256 access token for the AgileCards backend.

*** DEVELOPMENT TOOL. NEVER PART OF ANY PRODUCTION PATH. ***

Production tokens come from the Paradigm IdP (https://auth.paradigm.codes) and
nothing else. This script exists so the docker-compose dev profile can prove
the full stack end-to-end without that IdP (ADR-2026-07-16, decision 7):

1. Generates (or reuses) an RSA keypair under deploy/agilecards/dev-keys/.
   The directory is gitignored; the private key must never be committed.
2. Writes dev-keys/jwks.json (public key only, kid "dev-key-1"), which the
   compose ``dev-idp`` service serves at /.well-known/jwks.json.
3. Prints an RS256 JWT carrying every claim the backend verifier requires
   (iss, aud, exp, iat, nbf, sub, org_id, roles -- see cards_api/auth.py).

The backend only trusts these tokens when PARADIGM_JWKS_URL is explicitly
pointed at the dev-idp service. If that variable is unset (production), the
verifier fetches the real IdP's JWKS and dev tokens are rejected outright.

Usage (from backend/, inside its venv -- pyjwt+cryptography are base deps):

    python scripts/mint_dev_token.py --org org_acme --sub drew --roles admin,member
    python scripts/mint_dev_token.py --ttl 300          # short-lived, for SSE
    python scripts/mint_dev_token.py --print-claims     # show the payload too
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Must match the verifier's expectations (cards_api/config.py defaults).
DEFAULT_ISSUER = "https://auth.paradigm.codes"
DEFAULT_AUDIENCE = "paradigm-agilecards"
KID = "dev-key-1"

# backend/scripts/mint_dev_token.py -> repo root is parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEYS_DIR = REPO_ROOT / "deploy" / "agilecards" / "dev-keys"

PRIVATE_KEY_NAME = "dev_private_key.pem"
JWKS_NAME = "jwks.json"

BANNER = (
    "############################################################\n"
    "#  DEV TOKEN -- signed by a local throwaway key.           #\n"
    "#  Only a backend with PARADIGM_JWKS_URL pointed at the    #\n"
    "#  compose dev-idp service will accept it. Never valid     #\n"
    "#  against production, never use this script in prod.      #\n"
    "############################################################"
)


def _b64url_uint(value: int) -> str:
    """Base64url-encode an unsigned integer the way RFC 7518 JWKs want."""
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_or_create_keypair(keys_dir: Path) -> rsa.RSAPrivateKey:
    """Reuse the existing dev key if present so previously minted tokens stay
    valid across invocations; otherwise generate one and write key + JWKS."""
    keys_dir.mkdir(parents=True, exist_ok=True)
    key_path = keys_dir / PRIVATE_KEY_NAME

    if key_path.exists():
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise SystemExit(f"{key_path} is not an RSA private key; delete it and rerun.")
        return private_key

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print(f"generated new dev keypair: {key_path}", file=sys.stderr)
    return private_key


def write_jwks(keys_dir: Path, private_key: rsa.RSAPrivateKey) -> Path:
    """(Re)write jwks.json -- public material only, in the exact shape
    PyJWKClient resolves by ``kid``."""
    numbers = private_key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }
    jwks_path = keys_dir / JWKS_NAME
    jwks_path.write_text(json.dumps(jwks, indent=2) + "\n", encoding="utf-8")
    return jwks_path


def mint(args: argparse.Namespace, private_key: rsa.RSAPrivateKey) -> tuple[str, dict]:
    now = int(time.time())
    payload = {
        "iss": args.issuer,
        "aud": args.audience,
        "sub": args.sub,
        "iat": now,
        "nbf": now,
        "exp": now + args.ttl,
        "org_id": args.org,
        "roles": [r.strip() for r in args.roles.split(",") if r.strip()],
    }
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(payload, pem, algorithm="RS256", headers={"kid": KID})
    return token, payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mint a DEV-ONLY RS256 token for the AgileCards backend.",
        epilog="This tool is for local/dev compose stacks only. See module docstring.",
    )
    parser.add_argument("--org", default="org_dev", help="org_id claim (default: org_dev)")
    parser.add_argument("--sub", default="dev_user", help="sub claim (default: dev_user)")
    parser.add_argument(
        "--roles",
        default="member",
        help="comma-separated roles claim (default: member; card creation needs admin)",
    )
    parser.add_argument(
        "--ttl", type=int, default=3600, help="token lifetime in seconds (default: 3600)"
    )
    parser.add_argument("--issuer", default=DEFAULT_ISSUER, help=f"iss (default: {DEFAULT_ISSUER})")
    parser.add_argument(
        "--audience", default=DEFAULT_AUDIENCE, help=f"aud (default: {DEFAULT_AUDIENCE})"
    )
    parser.add_argument(
        "--keys-dir",
        type=Path,
        default=DEFAULT_KEYS_DIR,
        help=f"keypair/JWKS directory (default: {DEFAULT_KEYS_DIR})",
    )
    parser.add_argument(
        "--print-claims", action="store_true", help="also print the token payload to stderr"
    )
    args = parser.parse_args()

    private_key = load_or_create_keypair(args.keys_dir)
    jwks_path = write_jwks(args.keys_dir, private_key)

    token, payload = mint(args, private_key)

    print(BANNER, file=sys.stderr)
    print(f"jwks: {jwks_path}", file=sys.stderr)
    if args.print_claims:
        print(json.dumps(payload, indent=2), file=sys.stderr)
    # The token itself is the only stdout output, so shell capture is clean:
    #   TOKEN=$(python scripts/mint_dev_token.py --roles admin)
    print(token)


if __name__ == "__main__":
    main()
