import type { Config } from "tailwindcss";

/**
 * Tailwind config. The palette mirrors the v0 dashboard so the two
 * iterations look like cousins. Tier badges follow the same convention:
 * light = no extended thinking, dark = extended thinking.
 */
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "#0e1116",
        panel: "#161b22",
        panel2: "#1c222b",
        border: "#262d39",
        text: "#d7dde7",
        muted: "#7d8694",
        accent: "#7aa2f7",
        danger: "#f7768e",
        warn: "#e0af68",
        ok: "#9ece6a",
        tier: {
          1: "#5fb87b",
          2: "#3a8e58",
          3: "#5b8bd6",
          4: "#3a6bb8",
          5: "#a986d6",
          6: "#7e5cb3",
        },
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
