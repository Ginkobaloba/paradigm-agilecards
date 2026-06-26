/**
 * Single source of truth for runtime configuration. Read env once at
 * startup, validate, freeze. Anything that needs config imports from here
 * instead of reading process.env directly, so there's exactly one place
 * to look when something's misconfigured.
 */

import path from "node:path";

export interface Config {
  readonly port: number;
  readonly cardsDir: string;
  readonly dbPath: string;
  /**
   * Allowed CORS origins. Parsed from CORS_ORIGIN as a comma-separated
   * list so the same backend can serve both the standalone dev origin
   * and the Paradigm portal origin (which proxies through to /board/).
   */
  readonly corsOrigins: ReadonlyArray<string>;
  readonly logLevel: "error" | "warn" | "info" | "debug";
  /**
   * Paradigm portal federation (the "Gantry" embed). When the portal's
   * JWKS URL and issuer are both set, the board additionally accepts
   * portal-minted RS256 JWTs as bearer credentials, verified against the
   * portal JWKS per the portal Gate Contract. Leave unset for a
   * standalone deployment that uses only the local SQLite token store.
   */
  readonly portal: {
    readonly jwksUrl: string | null;
    readonly issuer: string | null;
    readonly audience: string;
    readonly enabled: boolean;
  };
}

function envStr(key: string, fallback: string): string {
  const v = process.env[key];
  return v && v.length > 0 ? v : fallback;
}

function envInt(key: string, fallback: number): number {
  const v = process.env[key];
  if (!v) return fallback;
  const n = Number.parseInt(v, 10);
  if (!Number.isFinite(n)) {
    throw new Error(`Env var ${key}=${v} is not an integer`);
  }
  return n;
}

function envLogLevel(): Config["logLevel"] {
  const v = (process.env["LOG_LEVEL"] ?? "info").toLowerCase();
  if (v === "error" || v === "warn" || v === "info" || v === "debug") return v;
  throw new Error(`LOG_LEVEL must be one of error|warn|info|debug, got ${v}`);
}

function envCorsOrigins(): ReadonlyArray<string> {
  const raw = envStr("CORS_ORIGIN", "http://localhost:5173");
  return Object.freeze(
    raw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
  );
}

function envStrOrNull(key: string): string | null {
  const v = process.env[key];
  return v && v.length > 0 ? v : null;
}

function envPortal(): Config["portal"] {
  const jwksUrl = envStrOrNull("PORTAL_JWKS_URL");
  const issuer = envStrOrNull("PORTAL_ISSUER");
  const audience = envStr("PORTAL_AUDIENCE", "gantry");
  return Object.freeze({
    jwksUrl,
    issuer,
    audience,
    // Federation is only live when both the key source and the issuer to
    // trust are configured. Audience alone is not enough.
    enabled: jwksUrl !== null && issuer !== null,
  });
}

const defaultCardsDir =
  process.platform === "win32" ? "C:\\dev\\todo" : path.resolve("./todo");

const defaultDbPath = path.resolve("./data/board.sqlite");

export const config: Config = Object.freeze({
  port: envInt("PORT", 4070),
  cardsDir: envStr("CARDS_DIR", defaultCardsDir),
  dbPath: envStr("DB_PATH", defaultDbPath),
  corsOrigins: envCorsOrigins(),
  logLevel: envLogLevel(),
  portal: envPortal(),
});
