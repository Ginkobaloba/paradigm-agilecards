/**
 * Gantry Tailwind preset.
 *
 * Source of truth: apps/board/brand/BRAND_HANDOFF_GANTRY_2026-06-18.md
 *
 * Consumers (the marketing landing today, the signed-in board frontend
 * tomorrow) extend their own `tailwind.config` with this preset so that
 * `gantry-forest`, `gantry-gunmetal`, etc. resolve consistently and so
 * `font-display`, `font-sans`, `font-mono` map to the brand fonts.
 *
 * Pair with `tokens.css` (imported once in the app's globals) to expose
 * the same values as CSS custom properties for non-Tailwind surfaces.
 */
module.exports = {
  theme: {
    extend: {
      colors: {
        // Brand primitives, named after the handoff doc.
        "gantry-forest": "#1f5a44",   // primary accent, active states
        "gantry-gunmetal": "#2a3439", // technical base, text
        "gantry-surface": "#f4f5f6",  // light industrial grey canvas
        "gantry-sage": "#2a6b4e",     // system success
        "gantry-slate": "#4a7abc",    // system info
      },
      fontFamily: {
        display: [
          "Fraunces",
          "Georgia",
          "Times New Roman",
          "serif",
        ],
        sans: [
          "Geist",
          "Geist Sans",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "Geist Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      letterSpacing: {
        // Logotype tracking per the brand handoff.
        logotype: "-0.04em",
      },
    },
  },
};
