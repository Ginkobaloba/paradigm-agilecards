# Gantry marketing landing

Standalone Vite + React + Tailwind app for the customer-facing **Gantry** marketing
landing. Lives alongside (not inside) the signed-in board frontend so that brand
changes can ship without touching the dashboard runtime.

## Layout

```
apps/board/
  brand/              -- shared brand primitives (tokens.css, tailwind.preset.cjs, assets)
  marketing/          -- this app (landing page)
  frontend/           -- signed-in board UI (extends the same brand preset)
  backend/            -- API for the signed-in board
```

## Develop

```powershell
cd C:\dev\agile-cards\apps\board\marketing
npm install
npm run dev     # serves on http://localhost:5174
npm run build   # tsc --noEmit + vite build to ./dist
```

## Where the brand lives

- Color, type, and tracking primitives: `apps/board/brand/tokens.css` and
  `apps/board/brand/tailwind.preset.cjs`. This app extends the preset and imports
  the tokens once in `src/styles/globals.css`.
- Logotype, motion, and social reference assets: `apps/board/brand/`. The PNG
  logotype is reserved for favicons and og:image only; the in-page wordmark is
  always rendered as Geist Mono Bold text with -0.04em tracking.

## Sections, in order

1. `Header.tsx` -- wordmark + nav, active-state `[ &#9646; ]` modifier in
   gantry-forest because the landing route is the "main" route.
2. `Hero.tsx` -- "Manage the outcome. Leave the process to us."
3. `DirectShift.tsx` -- chaotic chat vs. structured board, anchored on
   `gantry-motion-1.mp4`.
4. `PipelineLifecycle.tsx` -- "Write, drop, and review." Anchored on
   `gantry-motion-2.mp4`.
5. `FunctionalScenarios.tsx` -- engineering + back-office card panels.
6. `WorksWith.tsx` -- BYO-LLM vendor strip.
7. `FinalAction.tsx` -- "Clear out your administrative logjam." Email capture.
8. `Footer.tsx` -- inactive wordmark + section links.

## Public assets

- `public/favicon.svg` -- monochrome engineering schematic.
- `public/favicon.png` -- PNG fallback (logotype-2 for now).
- `public/og-image.png` -- social card image (logotype-2 for now; resize to
  1200x630 in a follow-up if needed).
- `public/brand-media/` -- copies of the source motion files referenced by
  the landing sections.
