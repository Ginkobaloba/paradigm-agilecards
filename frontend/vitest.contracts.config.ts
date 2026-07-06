import { defineConfig } from "vitest/config";

/**
 * Contract-test runner (CARDS-014, chunk K16). Kept separate from
 * vitest.config.ts (the Boards UI suite, which is jsdom + react and scoped to
 * src/) so the `board frontend battery` and the `contracts` CI job stay
 * independent. Node environment: the consumer contract for @paradigm/llm-client
 * is server-side (the Node BFF), with no DOM.
 */
export default defineConfig({
  test: {
    environment: "node",
    globals: false,
    include: ["contracts/**/*.test.ts"],
  },
});
