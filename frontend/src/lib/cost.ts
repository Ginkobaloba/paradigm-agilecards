/**
 * Frontend cost helpers. Mirrors `backend/src/cost/rates.ts` so the tile
 * can compute and format $ without round-tripping per card.
 *
 * The shape of the rate table comes from `GET /api/rates`. We keep the
 * pure-compute logic in this module so React components stay declarative
 * and the unit tests don't need a DOM.
 */

import type { CardSummary } from "./api";

export interface ModelRate {
  model: string;
  inputPerMTokens: number;
  outputPerMTokens: number;
  displayName?: string;
}

export interface RatesPayload {
  rates: ModelRate[];
  defaultInputRatio: number;
}

/**
 * The chip on the tile can be in one of these states. The component maps
 * each state to a Tailwind colour stack; this module only decides what
 * the state is.
 */
export type CostLevel = "ok" | "warn" | "danger";

const FALLBACK_RATE: ModelRate = {
  model: "unknown",
  displayName: "unknown",
  inputPerMTokens: 3,
  outputPerMTokens: 15,
};

const DEFAULT_INPUT_RATIO_FALLBACK = 0.6;

function rateFor(
  rates: readonly ModelRate[],
  model: string | null | undefined
): ModelRate {
  if (!model) return FALLBACK_RATE;
  return rates.find((r) => r.model === model) ?? FALLBACK_RATE;
}

/**
 * Compute USD cost from a token count. Supports either an aggregate
 * number (60/40 blended at defaultInputRatio) or a split
 * `{ input, output }` object.
 *
 * Returns 0 for empty / invalid inputs so callers can call this
 * unconditionally on every card.
 */
export function costForTokens(
  tokens:
    | number
    | null
    | undefined
    | { input?: number | null; output?: number | null },
  model: string | null | undefined,
  rates: readonly ModelRate[],
  defaultInputRatio: number = DEFAULT_INPUT_RATIO_FALLBACK
): number {
  const rate = rateFor(rates, model);
  if (tokens === null || tokens === undefined) return 0;

  if (typeof tokens === "number") {
    if (!Number.isFinite(tokens) || tokens <= 0) return 0;
    const input = tokens * defaultInputRatio;
    const output = tokens * (1 - defaultInputRatio);
    return (
      (input / 1_000_000) * rate.inputPerMTokens +
      (output / 1_000_000) * rate.outputPerMTokens
    );
  }

  const input = numOrZero(tokens.input);
  const output = numOrZero(tokens.output);
  return (
    (input / 1_000_000) * rate.inputPerMTokens +
    (output / 1_000_000) * rate.outputPerMTokens
  );
}

function numOrZero(v: number | null | undefined): number {
  if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return 0;
  return v;
}

/**
 * Compact USD formatter: under a dollar -> "$0.12"; under a hundred ->
 * "$3.45"; thousands -> "$1.2k". Designed for chips and column
 * headers where horizontal space is scarce.
 */
export function formatCost(usd: number): string {
  if (!Number.isFinite(usd) || usd <= 0) return "$0";
  if (usd < 0.01) return "<$0.01";
  if (usd < 100) return `$${usd.toFixed(2)}`;
  if (usd < 1000) return `$${usd.toFixed(0)}`;
  if (usd < 100_000) return `$${(usd / 1000).toFixed(1)}k`;
  return `$${(usd / 1_000_000).toFixed(1)}M`;
}

/**
 * Decide whether the chip is ok/warn/danger relative to a cap. The
 * roadmap calls for 80% -> warn, 100% -> danger. No cap -> always ok.
 */
export function costLevel(
  usd: number,
  cap: number | null | undefined
): CostLevel {
  if (cap === null || cap === undefined || !Number.isFinite(cap) || cap <= 0) {
    return "ok";
  }
  const ratio = usd / cap;
  if (ratio >= 1) return "danger";
  if (ratio >= 0.8) return "warn";
  return "ok";
}

/**
 * Per-card cost view model. `kind` tells the caller which figure was
 * surfaced — backlog cards show `est`, active/done show `spent`.
 *
 * If both estimate and actual are present (a card that's running with
 * a planned budget), we prefer `spent` because that's the live number
 * the operator cares about. The estimate stays available in the modal.
 */
export interface CardCost {
  usd: number;
  kind: "est" | "spent" | "none";
  cap: number | null;
  level: CostLevel;
  model: string | null;
}

export function cardCost(
  card: CardSummary,
  rates: readonly ModelRate[],
  defaultInputRatio: number = DEFAULT_INPUT_RATIO_FALLBACK
): CardCost {
  const fm = card.frontmatter;
  const actual = readPosNumber(fm["actual_tokens"]);
  const estimate = readPosNumber(fm["estimated_tokens"]);
  const cap = readPosNumber(fm["cost_cap_usd"]);
  // model_used is set by the runner at finish time; model is the planned
  // model. Prefer model_used for spent cards so historical pricing
  // matches what actually ran.
  const modelPlanned =
    typeof fm["model"] === "string" ? (fm["model"] as string) : null;
  const modelUsed =
    typeof fm["model_used"] === "string" ? (fm["model_used"] as string) : null;
  const model = modelUsed ?? modelPlanned;

  if (actual !== null) {
    const usd = costForTokens(actual, model, rates, defaultInputRatio);
    return { usd, kind: "spent", cap, level: costLevel(usd, cap), model };
  }
  if (estimate !== null) {
    const usd = costForTokens(estimate, model, rates, defaultInputRatio);
    return { usd, kind: "est", cap, level: costLevel(usd, cap), model };
  }
  return { usd: 0, kind: "none", cap, level: "ok", model };
}

function readPosNumber(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return null;
  return v;
}

/**
 * Sum the dollar cost of a list of cards. Used by the column header
 * rollup. `kind` of the rollup is the *plurality* kind — if most cards
 * are spent, label the rollup "spent"; otherwise "est".
 */
export interface ColumnRollup {
  usd: number;
  kind: "est" | "spent" | "mixed" | "none";
}

export function rollupCost(
  cards: readonly CardSummary[],
  rates: readonly ModelRate[],
  defaultInputRatio: number = DEFAULT_INPUT_RATIO_FALLBACK
): ColumnRollup {
  let total = 0;
  let est = 0;
  let spent = 0;
  for (const c of cards) {
    const cc = cardCost(c, rates, defaultInputRatio);
    if (cc.kind === "none") continue;
    total += cc.usd;
    if (cc.kind === "est") est++;
    if (cc.kind === "spent") spent++;
  }
  if (est === 0 && spent === 0) return { usd: 0, kind: "none" };
  if (spent === 0) return { usd: total, kind: "est" };
  if (est === 0) return { usd: total, kind: "spent" };
  return { usd: total, kind: "mixed" };
}
