import { defineConfig, loadEnv, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Vite config. Dev server proxies /api and /events to the backend so the
 * frontend can call relative URLs both in dev and in the docker build.
 *
 * The `@/*` import alias points at src/. Easier than long ../../ chains.
 *
 * Base path: VITE_BASE_PATH (default "/") controls where the app is
 * served from. Set it to "/board/" to host under the Paradigm portal
 * reverse proxy. Trailing slash matters: Vite uses `base` for both the
 * built asset URLs and `import.meta.env.BASE_URL`, and React Router
 * pulls `basename` from BASE_URL.
 */
export default defineConfig(({ mode }) => {
  // loadEnv picks up VITE_* from .env files plus shell env. The "" prefix
  // pulls everything; we narrow ourselves below.
  const env = loadEnv(mode, process.cwd(), "");
  const base = normalizeBase(env["VITE_BASE_PATH"] ?? "/");

  // When base is not "/", the frontend sends API calls prefixed with the
  // base path (e.g. "/board/api/cards"). The Express backend still mounts
  // routes at "/api", "/events", "/healthz". Strip the base prefix on the
  // dev proxy so requests reach the backend's real paths.
  const proxyTarget = "http://localhost:4070";
  const stripBase = base === "/" ? undefined : stripBaseRewrite(base);
  const proxy = (extra: Partial<ProxyOptions> = {}): ProxyOptions => ({
    target: proxyTarget,
    changeOrigin: true,
    ...(stripBase ? { rewrite: stripBase } : {}),
    ...extra,
  });

  // Two proxy keys per upstream path: one for the "raw" path so a direct
  // dev call works, one for the base-prefixed path so a portal-style
  // call works. When base is "/" the two collapse to the same key, which
  // Vite tolerates because the second write wins.
  const proxyConfig: Record<string, ProxyOptions> = {
    "/api": proxy(),
    "/events": proxy({ ws: false }),
    "/healthz": proxy(),
  };
  if (base !== "/") {
    proxyConfig[`${stripTrailingSlash(base)}/api`] = proxy();
    proxyConfig[`${stripTrailingSlash(base)}/events`] = proxy({ ws: false });
    proxyConfig[`${stripTrailingSlash(base)}/healthz`] = proxy();
  }

  return {
    base,
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      strictPort: true,
      // Vite 5.4.12+ blocks unknown Host headers by default, which breaks
      // access through a Cloudflare quick-tunnel or named tunnel because
      // those rewrite Host to the public hostname. Allow loopback plus
      // any *.trycloudflare.com (quick tunnels) and the persistent app
      // and portal hostnames for the named tunnels. Add more entries as
      // needed.
      allowedHosts: [
        "localhost",
        "127.0.0.1",
        ".trycloudflare.com",
        "app.projectnexuscode.org",
        "portal.projectnexuscode.org",
      ],
      proxy: proxyConfig,
    },
    build: {
      sourcemap: true,
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});

function normalizeBase(raw: string): string {
  if (!raw || raw === "/") return "/";
  let v = raw.trim();
  if (!v.startsWith("/")) v = `/${v}`;
  if (!v.endsWith("/")) v = `${v}/`;
  return v;
}

function stripTrailingSlash(v: string): string {
  return v.endsWith("/") ? v.slice(0, -1) : v;
}

function stripBaseRewrite(base: string): (p: string) => string {
  // Strip the configured base path off incoming dev proxy requests so
  // "/board/api/cards" reaches the backend as "/api/cards".
  const prefix = stripTrailingSlash(base);
  return (p) => (p.startsWith(prefix) ? p.slice(prefix.length) || "/" : p);
}
