import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite config for the Gantry marketing landing.
 *
 * The app serves at the project root by default. If you mount it under a
 * subpath at deploy time, set `BASE_URL` in the deploy environment and
 * Vite will pick it up via `import.meta.env.BASE_URL`.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    host: true,
  },
  preview: {
    port: 5174,
    host: true,
  },
});
