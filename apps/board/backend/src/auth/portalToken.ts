/**
 * Paradigm portal federation verifier ("Gantry" embed).
 *
 * The portal mints short-lived RS256 JWTs and publishes its public keys
 * at a JWKS endpoint (see the portal Gate Contract). When a logged-in
 * portal user launches Gantry, the portal redirects to
 * `<board>/#portal_token=<JWT>`; the board frontend stores that JWT as
 * its bearer, and this module verifies it on the backend.
 *
 * Verification rules (all mandatory per the contract):
 *   - RS256 signature against a key from the portal JWKS (matched by kid).
 *   - `iss` exactly equals the configured portal issuer.
 *   - `aud` exactly equals the configured app slug ("gantry").
 *   - `exp` is in the future (jose enforces this).
 *
 * The JWKS is fetched lazily and cached by jose's remote key set, which
 * also handles rotation: an unknown kid triggers a re-fetch (cooldown
 * limited), so a portal key rotation is picked up without a board
 * restart.
 */

import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";

import { config } from "../config.js";
import { log } from "../logger.js";

export interface PortalClaims {
  /** Canonical lowercased user email (the JWT `sub`). */
  readonly email: string;
  /** Tenant id, or null for staff/internal with no single tenant. */
  readonly customerId: string | null;
  /** Portal role. */
  readonly role: string;
}

// Lazily built so a standalone deployment (federation disabled) never
// constructs a key set or makes a network call.
let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;

function getJwks(): ReturnType<typeof createRemoteJWKSet> | null {
  if (!config.portal.enabled || !config.portal.jwksUrl) return null;
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(config.portal.jwksUrl));
  }
  return jwks;
}

/**
 * A bearer credential is a candidate portal JWT only if it has the
 * three-segment `a.b.c` shape. The local SQLite tokens are opaque random
 * strings with no dots, so this cheaply routes credentials to the right
 * verifier without a wasted crypto attempt.
 */
export function looksLikeJwt(token: string): boolean {
  const parts = token.split(".");
  return parts.length === 3 && parts.every((p) => p.length > 0);
}

/**
 * Verify a portal-minted JWT. Returns the claims on success, or null on
 * any failure (bad signature, wrong issuer/audience, expired, JWKS
 * unreachable). Never throws; the caller treats null as "not a valid
 * portal credential" and responds 401.
 */
export async function verifyPortalToken(
  token: string,
): Promise<PortalClaims | null> {
  const keys = getJwks();
  if (!keys || !config.portal.issuer) return null;

  try {
    const { payload } = await jwtVerify(token, keys, {
      issuer: config.portal.issuer,
      audience: config.portal.audience,
      algorithms: ["RS256"],
    });
    return claimsFromPayload(payload);
  } catch (err) {
    log.debug("portal token verify failed", {
      reason: err instanceof Error ? err.message : String(err),
    });
    return null;
  }
}

function claimsFromPayload(payload: JWTPayload): PortalClaims | null {
  const email = typeof payload.sub === "string" ? payload.sub : "";
  if (!email) return null;
  const customerId =
    typeof payload.customer_id === "string" ? payload.customer_id : null;
  const role = typeof payload.role === "string" ? payload.role : "customer";
  return { email, customerId, role };
}
