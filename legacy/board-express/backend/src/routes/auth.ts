/**
 * Bearer-token middleware. Extracts the token from the Authorization
 * header (preferred) or the `token` query string (only used by SSE,
 * because EventSource doesn't let you set headers).
 *
 * On valid token, attaches { tokenId, tokenLabel } to res.locals.auth so
 * downstream handlers can log who did what.
 */

import type { NextFunction, Request, Response } from "express";

import { validateToken } from "../auth/tokens.js";
import { config } from "../config.js";
import { looksLikeJwt, verifyPortalToken } from "../auth/portalToken.js";

export interface AuthContext {
  readonly tokenId: number;
  readonly tokenLabel: string;
}

/**
 * Typed accessor for res.locals.auth. Avoids a global module
 * augmentation, which fights the existing Express typings.
 */
export function getAuthContext(
  res: { locals: Record<string, unknown> }
): AuthContext | undefined {
  const a = res.locals["auth"];
  if (
    a &&
    typeof a === "object" &&
    typeof (a as AuthContext).tokenId === "number" &&
    typeof (a as AuthContext).tokenLabel === "string"
  ) {
    return a as AuthContext;
  }
  return undefined;
}

function extractToken(req: Request): string | null {
  const header = req.header("authorization") ?? req.header("Authorization");
  if (header) {
    const m = /^Bearer\s+(.+)$/i.exec(header);
    if (m && m[1]) return m[1].trim();
  }
  const q = req.query["token"];
  if (typeof q === "string" && q.length > 0) return q;
  return null;
}

/**
 * Sentinel token id for credentials that did not come from the local
 * SQLite store (portal-federated JWTs). Keeps getAuthContext's "tokenId
 * is a number" invariant while marking the row as not-from-our-store.
 */
const PORTAL_TOKEN_ID = -1;

export function requireAuth(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  // Express ignores a returned promise, so own the rejection here and
  // turn any unexpected failure into a clean 401 rather than an unhandled
  // rejection that crashes the process.
  void authenticate(req, res, next).catch(() => {
    if (!res.headersSent) {
      res.status(401).json({ error: "invalid bearer token" });
    }
  });
}

async function authenticate(
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> {
  const plaintext = extractToken(req);
  if (!plaintext) {
    res.status(401).json({ error: "missing bearer token" });
    return;
  }

  // Local opaque token first: synchronous, no network, the common path
  // for the admin/laptop tokens minted via create-token.
  const row = validateToken(plaintext);
  if (row) {
    res.locals["auth"] = { tokenId: row.id, tokenLabel: row.label };
    next();
    return;
  }

  // Portal federation fallback: a JWT-shaped bearer is verified against
  // the portal JWKS. Only attempted when federation is configured and the
  // credential actually has the three-segment JWT shape.
  if (config.portal.enabled && looksLikeJwt(plaintext)) {
    const claims = await verifyPortalToken(plaintext);
    if (claims) {
      res.locals["auth"] = {
        tokenId: PORTAL_TOKEN_ID,
        tokenLabel: `portal:${claims.email}`,
      };
      next();
      return;
    }
  }

  res.status(401).json({ error: "invalid bearer token" });
}
