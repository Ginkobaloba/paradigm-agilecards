/**
 * Tailwind config for the Gantry marketing landing.
 *
 * Extends the shared Gantry brand preset (apps/board/brand/tailwind.preset.cjs)
 * so that gantry-forest, gantry-gunmetal, gantry-surface, gantry-sage, gantry-slate
 * and font-display / font-sans / font-mono are all wired the same way the
 * signed-in board frontend will wire them.
 */
const brandPreset = require("../brand/tailwind.preset.cjs");

/** @type {import('tailwindcss').Config} */
module.exports = {
  presets: [brandPreset],
  content: ["./index.html", "./src/**/*.{ts,tsx,jsx,js}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
