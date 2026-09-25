"""Deep verify for paradigm-agilecards fix/drop-infisical-provider.

Boots the REAL backend (uvicorn app:app) as a subprocess under each
PARADIGM_SECRETS_PROVIDER setting and exercises it over HTTP, with JWTs signed
by a throwaway RSA key served from a local JWKS endpoint. Unit tests call
load_settings() directly; this proves the process-level behavior.

Run from backend/ with the backend venv's python.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def b64u(n):
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


# ---- throwaway signing key + JWKS server ---------------------------------------------
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pub = key.public_key().public_numbers()
KID = "deep-verify-kid"
JWKS = {"keys": [{"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256", "n": b64u(pub.n), "e": b64u(pub.e)}]}


class JwksHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(JWKS).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


jwks_port = free_port()
jwks_srv = HTTPServer(("127.0.0.1", jwks_port), JwksHandler)
threading.Thread(target=jwks_srv.serve_forever, daemon=True).start()
ISSUER = f"http://127.0.0.1:{jwks_port}"
AUDIENCE = "deep-verify-audience"


def token(**over):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-1", "org_id": "org-a",
              "roles": ["admin"], "iat": now, "nbf": now, "exp": now + 300}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


def get(port, path, bearer=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def boot(extra_env, expect_up):
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PARADIGM_")}
    env.update(extra_env)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not expect_up:
        # "Refuses to boot" means the process exits. Probing the port is wrong here:
        # a connect can succeed against an unrelated listener (a first run of this
        # harness did exactly that and reported a false boot).
        try:
            _, stderr = proc.communicate(timeout=20)
            return port, proc, stderr
        except subprocess.TimeoutExpired:
            return port, proc, None
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            return port, proc, proc.stderr.read()
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return port, proc, None
        except OSError:
            time.sleep(0.3)
    return port, proc, "timeout"


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---- scenario A: env-only config drives live auth -------------------------------------
port, proc, err = boot({"PARADIGM_JWT_ISSUER": ISSUER, "PARADIGM_JWT_AUDIENCE": AUDIENCE}, True)
check("A1 boots with provider unset (env)", err is None, err or "")
if err is None:
    s, _ = get(port, "/healthz"); check("A2 /healthz", s == 200, f"status={s}")
    s, _ = get(port, "/api/me"); check("A3 /api/me without token -> 401", s == 401, f"status={s}")
    s, body = get(port, "/api/me", token()); check("A4 valid token for env issuer+audience -> 200", s == 200 and body and body.get("org_id") == "org-a", f"status={s} body={body}")
    s, _ = get(port, "/api/me", token(aud="paradigm-agilecards")); check("A5 token for the DEFAULT audience -> rejected (env value is live)", s == 401, f"status={s}")
    s, _ = get(port, "/api/me", token(iss="https://auth.paradigm.codes")); check("A6 token for the DEFAULT issuer -> rejected", s == 401, f"status={s}")
    s, _ = get(port, "/api/me", token(exp=int(time.time()) - 60)); check("A7 expired token -> 401", s == 401, f"status={s}")
    none_tok = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "org_id": "o", "iat": int(time.time()), "exp": int(time.time()) + 300}, None, algorithm="none")
    s, _ = get(port, "/api/me", none_tok); check("A8 alg=none token -> 401 (adversarial)", s == 401, f"status={s}")
    s, _ = get(port, "/api/me", token(org_id="")); check("A9 missing org_id -> 401", s == 401, f"status={s}")
stop(proc)

# ---- scenario B/C: removed / unknown provider fail closed at boot -----------------------
for label, val, needle in [("B infisical", "infisical", "no longer supported"),
                           ("B' Infisical (case)", " INFISICAL ", "no longer supported"),
                           ("C unknown 'vault'", "vault", "Unknown PARADIGM_SECRETS_PROVIDER")]:
    port, proc, err = boot({"PARADIGM_SECRETS_PROVIDER": val, "PARADIGM_JWT_AUDIENCE": AUDIENCE}, False)
    exited = proc.poll() is not None and proc.returncode != 0
    check(f"{label} -> process refuses to boot with fix-it message", exited and err and needle in err,
          f"rc={proc.returncode} msg={'found' if (err and needle in err) else 'MISSING'}")
    stop(proc)

# ---- scenario D: explicit env (blank/case) boots -------------------------------------
port, proc, err = boot({"PARADIGM_SECRETS_PROVIDER": " ENV ", "PARADIGM_JWT_ISSUER": ISSUER, "PARADIGM_JWT_AUDIENCE": AUDIENCE}, True)
check("D1 PARADIGM_SECRETS_PROVIDER=' ENV ' boots", err is None, err or "")
if err is None:
    s, _ = get(port, "/api/me", token()); check("D2 and serves auth from env values", s == 200, f"status={s}")
stop(proc)

# ---- scenario E: no Infisical SDK anywhere --------------------------------------------
import importlib.util
check("E1 infisical_client not installed in this env", importlib.util.find_spec("infisical_client") is None)

jwks_srv.shutdown()
failed = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
sys.exit(1 if failed else 0)
