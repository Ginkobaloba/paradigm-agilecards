/**
 * Deployment branding. The board is a single product (agile-cards-board)
 * but a given deployment can present under a customer-facing brand -- the
 * Paradigm portal embed runs as "Gantry". Brand is build-time config so
 * the product itself is not renamed; only this deployment's image carries
 * the override.
 *
 * Set VITE_APP_BRAND (and optionally VITE_APP_TAGLINE) at build time. When
 * unset, the defaults keep the standalone product identity.
 */

function envTrimmed(v: unknown, fallback: string): string {
  return typeof v === "string" && v.trim().length > 0 ? v.trim() : fallback;
}

export const APP_BRAND: string = envTrimmed(
  import.meta.env.VITE_APP_BRAND,
  "agile-cards",
);

export const APP_TAGLINE: string = envTrimmed(
  import.meta.env.VITE_APP_TAGLINE,
  "board v0+",
);
