/**
 * Paradigm portal handoff ("Gantry" embed).
 *
 * When a logged-in portal user launches Gantry, the portal redirects to
 * `<board>/#portal_token=<JWT>`. The token rides in the URL fragment so
 * it never reaches an HTTP access log (see the portal Gate Contract).
 *
 * On boot we read that fragment, store the JWT as the board's bearer
 * token (the same sessionStorage slot the manual token gate uses), then
 * scrub it from the URL so a copied link or a reload does not leak or
 * replay the token.
 */

import { setToken } from "./auth";

const FRAGMENT_KEY = "portal_token";

/**
 * Pull `portal_token` out of a raw `location.hash` value. Pure so it can
 * be unit tested. Accepts hashes with or without the leading `#` and
 * tolerates other fragment params alongside the token.
 */
export function parsePortalToken(rawHash: string): string | null {
  const hash = rawHash.startsWith("#") ? rawHash.slice(1) : rawHash;
  if (hash.length === 0) return null;
  const params = new URLSearchParams(hash);
  const token = params.get(FRAGMENT_KEY);
  return token && token.length > 0 ? token : null;
}

/**
 * Remove only the `portal_token` entry from a raw hash, preserving any
 * other fragment state. Returns the new hash body (no leading `#`), or
 * "" when nothing is left.
 */
export function stripPortalToken(rawHash: string): string {
  const hash = rawHash.startsWith("#") ? rawHash.slice(1) : rawHash;
  const params = new URLSearchParams(hash);
  params.delete(FRAGMENT_KEY);
  return params.toString();
}

/**
 * Side-effecting boot step. If the current URL fragment carries a portal
 * token, store it and scrub the fragment. Returns true when a token was
 * claimed. Safe to call when there is no fragment (no-op).
 */
export function claimPortalToken(): boolean {
  if (typeof window === "undefined") return false;
  const token = parsePortalToken(window.location.hash);
  if (!token) return false;

  setToken(token);

  const remaining = stripPortalToken(window.location.hash);
  const newUrl =
    window.location.pathname +
    window.location.search +
    (remaining.length > 0 ? `#${remaining}` : "");
  window.history.replaceState(null, "", newUrl);
  return true;
}
