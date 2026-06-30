/**
 * Pure math + classification for the dual-axis grid view.
 *
 * The Grid route plots each card at (x, y) on a normalized [0, 1] plane.
 * This module decides:
 *   - what numeric value a card has for a given axis (`axisValue`)
 *   - how to normalize a list of those values (`normalize`)
 *   - which quadrant a normalized point lands in (`classifyQuadrant`)
 *   - what stakes string a normalized Y maps back to (`snapStakes`)
 *   - what color a project key gets (`projectColor`)
 *
 * Everything is pure -- no DOM, no fetch. The grid component is the
 * one that owns layout, drag handling, and the React rendering.
 *
 * Convention: the math here treats Y=1 as "high" (top of the plot).
 * The component is responsible for flipping to screen coordinates
 * (where Y=0 is the top of the viewport).
 */

import type { CardSummary } from "./api";
import { cardCost, type RatesPayload } from "./cost";
import { cardPoints, cardStakes } from "./parseCard";

export type AxisKey = "cost" | "stakes" | "points" | "tier";

export interface AxisOption {
  readonly key: AxisKey;
  readonly label: string;
  /** Whether dragging along this axis writes back to the card. */
  readonly editable: boolean;
}

export const AXIS_OPTIONS: ReadonlyArray<AxisOption> = [
  { key: "cost", label: "$ spend", editable: false },
  { key: "stakes", label: "Stakes", editable: true },
  { key: "points", label: "Points", editable: false },
  { key: "tier", label: "Tier", editable: false },
];

export const STAKES_ORDER = ["low", "medium", "high"] as const;
export type Stakes = (typeof STAKES_ORDER)[number];

export function isStakes(v: unknown): v is Stakes {
  return typeof v === "string" && (STAKES_ORDER as readonly string[]).includes(v);
}

/**
 * Raw numeric value for a card on a given axis. Returns null for cards
 * that lack a value (e.g. no stakes set, or no token data for cost).
 * The caller decides how to render nulls; the Grid component drops
 * them into an "ungraphed" tray rather than guessing a coordinate.
 */
export function axisValue(
  card: CardSummary,
  axis: AxisKey,
  rates: RatesPayload
): number | null {
  switch (axis) {
    case "cost": {
      const cc = cardCost(card, rates.rates, rates.defaultInputRatio);
      if (cc.kind === "none") return null;
      return cc.usd;
    }
    case "stakes": {
      const raw = cardStakes(card);
      if (typeof raw !== "string") return null;
      const lower = raw.toLowerCase();
      if (!isStakes(lower)) return null;
      return STAKES_ORDER.indexOf(lower);
    }
    case "points":
      return cardPoints(card);
    case "tier":
      // The board has no first-class `tier` field; the planner uses
      // `points` as the tier rank, so we re-use it here. When a real
      // tier surfaces (chunk 2 of the ledger?), swap this case.
      return cardPoints(card);
  }
}

export type AxisScale = "linear" | "log" | "ordinal";

/**
 * Normalize a list of axis values into [0, 1] in the same order. Null
 * inputs stay null in the output so the caller can decide where to
 * render them.
 *
 *   linear:  min..max -> 0..1                   (constants map to 0.5)
 *   log:     log10(v + 1) for the same range    (good for $ where most
 *                                                 cards cluster low)
 *   ordinal: each unique value gets an even slot (good for stakes
 *                                                 / tier / points)
 *
 * Negative inputs are clamped to 0 inside the log branch to keep
 * log10 well-defined; otherwise the math is as-stated.
 */
export function normalize(
  values: ReadonlyArray<number | null>,
  scale: AxisScale
): Array<number | null> {
  const real = values.filter(
    (v): v is number => v !== null && Number.isFinite(v)
  );
  if (real.length === 0) return values.map(() => null);

  if (scale === "ordinal") {
    const unique = Array.from(new Set(real)).sort((a, b) => a - b);
    const last = unique.length - 1;
    if (last === 0) {
      return values.map((v) => (v === null ? null : 0.5));
    }
    return values.map((v) =>
      v === null ? null : unique.indexOf(v) / last
    );
  }

  const transform = (v: number): number =>
    scale === "log" ? Math.log10(Math.max(v, 0) + 1) : v;
  const transformed = real.map(transform);
  const lo = Math.min(...transformed);
  const hi = Math.max(...transformed);
  if (hi === lo) return values.map((v) => (v === null ? null : 0.5));

  return values.map((v) => {
    if (v === null) return null;
    return (transform(v) - lo) / (hi - lo);
  });
}

export function defaultScaleFor(axis: AxisKey): AxisScale {
  switch (axis) {
    case "cost":
      return "log";
    case "stakes":
    case "points":
    case "tier":
      return "ordinal";
  }
}

export type Quadrant = "priority" | "do-carefully" | "backlog" | "cancel";

/**
 * Classify a normalized (x, y) point into one of the four spend-side
 * quadrants. The y axis is "value" (high y = important), the x axis is
 * "cost" (high x = expensive). The Grid component is responsible for
 * making sure the axes are oriented that way before classifying.
 *
 *   priority:      high y, low x  (do this first)
 *   do-carefully:  high y, high x (worth doing, mind the budget)
 *   backlog:       low y, low x   (whenever)
 *   cancel:        low y, high x  (downtier or kill)
 */
export function classifyQuadrant(
  x: number,
  y: number,
  cutoff: number = 0.5
): Quadrant {
  const highY = y >= cutoff;
  const highX = x >= cutoff;
  if (highY && !highX) return "priority";
  if (highY && highX) return "do-carefully";
  if (!highY && !highX) return "backlog";
  return "cancel";
}

export const QUADRANT_LABEL: Record<Quadrant, string> = {
  priority: "High value / low spend -- priority",
  "do-carefully": "High value / high spend -- do carefully",
  backlog: "Low value / low spend -- backlog",
  cancel: "Low value / high spend -- cancel or downtier",
};

/**
 * Snap a normalized Y value (math convention: 0 = low at bottom, 1 =
 * high at top) to the nearest stakes bucket. The Grid drag handler
 * calls this to translate a drop position into a value to write back
 * to the card's `stakes` field.
 */
export function snapStakes(yNorm: number): Stakes {
  const clamped = Math.min(1, Math.max(0, yNorm));
  const idx = Math.round(clamped * (STAKES_ORDER.length - 1));
  return STAKES_ORDER[idx]!;
}

/**
 * Clamp to [0, 1], collapsing non-finite inputs to 0 so a bad delta can
 * never propagate a NaN into a coordinate.
 */
export function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.min(1, Math.max(0, v));
}

/**
 * Coordinate flips between math-Y (0 = bottom, 1 = top, the convention
 * this module uses) and screen-Y (0 = top, 1 = bottom, what the DOM and
 * dnd-kit's delta speak). They're involutions, but naming both makes the
 * call sites in the drag handler read in the direction they convert.
 */
export function screenYFromMath(yMath: number): number {
  return 1 - yMath;
}
export function mathYFromScreen(yScreen: number): number {
  return 1 - yScreen;
}

export interface RestakeInput {
  /** Current math-Y of the dragged point in [0, 1] (0 = low, 1 = high). */
  yNorm: number;
  /** dnd-kit's `delta.y` in screen pixels (positive = dragged downward). */
  deltaYPx: number;
  /** Pixel height of the plot, from its bounding rect at drop time. */
  plotHeightPx: number;
  /** The card's current stakes, already lowercased, or null if unset. */
  currentStakes: string | null;
}

/**
 * Decide what stakes value (if any) a drag should write back. Pure, so
 * the Grid component's drag handler stays a thin shell over this and the
 * coordinate math is unit-tested without a DOM or a real dnd-kit event.
 *
 * Returns null when there is nothing to write: a zero/invalid plot
 * height (can't translate the delta into a fraction), or a drag that
 * lands in the same bucket the card already has (avoid a needless disk
 * write and watcher churn).
 */
export function restakeFromDrag(
  input: RestakeInput
): { targetStakes: Stakes } | null {
  const { yNorm, deltaYPx, plotHeightPx, currentStakes } = input;
  if (!Number.isFinite(plotHeightPx) || plotHeightPx <= 0) return null;
  // dnd-kit's delta is in screen pixels. Convert to a plot-relative
  // fraction, apply it in screen space, then flip back to math space.
  const dyFrac = deltaYPx / plotHeightPx;
  const newYNormScreen = clamp01(screenYFromMath(yNorm) + dyFrac);
  const newYNorm = mathYFromScreen(newYNormScreen);
  const targetStakes = snapStakes(newYNorm);
  if (targetStakes === currentStakes) return null;
  return { targetStakes };
}

/**
 * Stable color for a project key, picked from a 12-slot palette via a
 * djb2 hash so the same project always renders the same color across
 * sessions. The "Unassigned" bucket gets a neutral muted swatch so it
 * doesn't compete visually with real projects.
 */
const PROJECT_PALETTE: readonly string[] = [
  "#60a5fa", "#f472b6", "#34d399", "#fbbf24",
  "#a78bfa", "#f87171", "#22d3ee", "#fb923c",
  "#4ade80", "#e879f9", "#facc15", "#38bdf8",
];

export const UNASSIGNED_COLOR = "#6b7280";

export function projectColor(projectKey: string): string {
  if (projectKey === "Unassigned") return UNASSIGNED_COLOR;
  let h = 5381;
  for (let i = 0; i < projectKey.length; i++) {
    h = ((h << 5) + h + projectKey.charCodeAt(i)) >>> 0;
  }
  return PROJECT_PALETTE[h % PROJECT_PALETTE.length]!;
}
