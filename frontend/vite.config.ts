import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Vite config. Dev server proxies /api and /events to the backend so the
 * frontend can call relative URLs both in dev and in the docker build.
 *
 * The `@/*` import alias points at src/. Easier than long ../../ chains.
 */
export default defineConfig({
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
    // hostname for the named tunnel. Add more entries as needed.
    allowedHosts: [
      "localhost",
      "127.0.0.1",
      ".trycloudflare.com",
      "app.projectnexuscode.org",
    ],
    proxy: {
      "/api": {
        target: "http://localhost:4070",
        changeOrigin: true,
      },
      "/events": {
        target: "http://localhost:4070",
        changeOrigin: true,
        // SSE needs no buffering.
        ws: false,
      },
      "/healthz": {
        target: "http://localhost:4070",
        changeOrigin: true,
      },
    },
  },
  build: {
    sourcemap: true,
    outDir: "dist",
    emptyOutDir: true,
  },
});
