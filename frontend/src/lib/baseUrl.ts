/**
 * Base-path helpers. The Paradigm portal serves this app under
 * `/board/` via a reverse proxy. Vite's `base` config and React
 * Router's `basename` both need that prefix; backend calls need it too
 * so the proxy routes them correctly.
 *
 * Single source of truth: `import.meta.env.BASE_URL`, which Vite
 * derives from the build-time `base` option (set via VITE_BASE_PATH).
 * It always has a trailing slash, e.g. "/" or "/board/".
 *
 * The pure helpers (`deriveRouterBasename`, `buildApiPath`) are tested
 * directly; the exported `routerBasename` and `apiPath` close over the
 * Vite-injected `BASE_URL`.
 */

/**
 * Strip a trailing slash. "/" -> "", "/board/" -> "/board".
 */
function trimTrailingSlash(v: string): string {
  return v.endsWith("/") ? v.slice(0, -1) : v;
}

/**
 * Derive a `basename` value suitable for `<BrowserRouter basename={...}>`.
 * React Router accepts "/" cleanly but a bare "" is ambiguous in some
 * paths, so collapse the root case to "/".
 */
export function deriveRouterBasename(rawBase: string | undefined): string {
  const raw = typeof rawBase === "string" && rawBase.length > 0 ? rawBase : "/";
  const trimmed = trimTrailingSlash(raw);
  return trimmed.length > 0 ? trimmed : "/";
}

/**
 * Prefix a backend-relative path (e.g. "/api/cards", "/events",
 * "/healthz") with `rawBase`. The path must start with "/" so we don't
 * accidentally swallow the separator.
 */
export function buildApiPath(rawBase: string | undefined, p: string): string {
  if (!p.startsWith("/")) {
    throw new Error(`apiPath() requires a path starting with "/", got: ${p}`);
  }
  const raw = typeof rawBase === "string" && rawBase.length > 0 ? rawBase : "/";
  const trimmed = trimTrailingSlash(raw);
  return `${trimmed}${p}`;
}

const RAW_BASE: string =
  typeof import.meta.env.BASE_URL === "string" && import.meta.env.BASE_URL.length > 0
    ? import.meta.env.BASE_URL
    : "/";

/**
 * BrowserRouter basename derived from `import.meta.env.BASE_URL`. "/" at
 * the root, "/board" (etc.) when running under a base path.
 */
export const routerBasename: string = deriveRouterBasename(RAW_BASE);

/**
 * Prefix a backend-relative path with the configured base path.
 */
export function apiPath(p: string): string {
  return buildApiPath(RAW_BASE, p);
}
