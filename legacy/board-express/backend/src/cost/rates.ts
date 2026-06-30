/**
 * Per-model USD rates for converting token counts into dollar cost.
 *
 * The cards on disk record token usage (`estimated_tokens` and, once a
 * runner has finished, `actual_tokens`) but never store dollars. The
 * dashboard computes cost on demand from tokens times rate. Reasoning is
 * in roadmap fork F: rate-table-on-the-backend means a rate change
 * automatically reprices history, instead of leaving stale dollar figures
 * baked into frontmatter. In-flight cost is also computable, which we'll
 * need once we surface live `claimed_by` runners.
 *
 * Rates are USD per 1M tokens. They roughly track the public Anthropic
 * pricing as of 2026-05; close enough to be useful for budgeting without
 * needing to be perfect. Anthropic publishes split input/output prices,
 * so we keep them split here; for cards that only store a single
 * aggregate `*_tokens` number we apply a 60/40 input/output blend that
 * matches the median observed mix on a planning agent (the planner reads
 * more than it writes).
 *
 * When the runner starts emitting separate `input_tokens` and
 * `output_tokens` per card, the frontend will switch to those fields and
 * skip the blend.
 */

export interface ModelRate {
  /** Canonical model id used by the runner (matches `model` / `model_used`). */
  readonly model: string;
  /** USD per 1,000,000 input tokens. */
  readonly inputPerMTokens: number;
  /** USD per 1,000,000 output tokens. */
  readonly outputPerMTokens: number;
  /** Optional alias display name (mainly for the rate table response). */
  readonly displayName?: string;
}

/**
 * Hard-coded rate table for v1. Single source of truth — the
 * `GET /api/rates` route returns this verbatim so the frontend can format
 * tiles offline if needed and so a future yaml-backed rate file is a
 * drop-in replacement.
 *
 * Add new models by appending; don't reorder, the frontend doesn't care
 * but stable order keeps diffs small.
 */
export const MODEL_RATES: readonly ModelRate[] = [
  {
    model: "claude-opus-4-7",
    displayName: "Opus 4.7",
    inputPerMTokens: 15,
    outputPerMTokens: 75,
  },
  {
    model: "claude-opus-4-6",
    displayName: "Opus 4.6",
    inputPerMTokens: 15,
    outputPerMTokens: 75,
  },
  {
    model: "claude-sonnet-4-6",
    displayName: "Sonnet 4.6",
    inputPerMTokens: 3,
    outputPerMTokens: 15,
  },
  {
    model: "claude-sonnet-4-5",
    displayName: "Sonnet 4.5",
    inputPerMTokens: 3,
    outputPerMTokens: 15,
  },
  {
    model: "claude-haiku-4-5",
    displayName: "Haiku 4.5",
    inputPerMTokens: 1,
    outputPerMTokens: 5,
  },
];

/**
 * Default blend ratio (60% input, 40% output) used when a card stores
 * only an aggregate token count and we don't know the I/O split.
 */
export const DEFAULT_INPUT_RATIO = 0.6;

/**
 * Fallback rate when the model is unknown. Picked to be neither cheap nor
 * expensive — Sonnet-class — so a missing model entry doesn't make a
 * column look free or look catastrophically expensive.
 */
export const FALLBACK_RATE: ModelRate = {
  model: "unknown",
  displayName: "unknown",
  inputPerMTokens: 3,
  outputPerMTokens: 15,
};

const BY_MODEL: Map<string, ModelRate> = new Map(
  MODEL_RATES.map((r) => [r.model, r])
);

/**
 * Look up the rate for a model id. Unknown models fall back to a
 * sonnet-class blend — see FALLBACK_RATE.
 */
export function rateFor(model: string | null | undefined): ModelRate {
  if (!model) return FALLBACK_RATE;
  return BY_MODEL.get(model) ?? FALLBACK_RATE;
}

/**
 * Compute USD cost for a token count. Caller passes either a split
 * `{ input, output }` or an aggregate; aggregates apply DEFAULT_INPUT_RATIO.
 *
 * Returns 0 for zero or invalid inputs so the call site doesn't need to
 * special-case. NaN-in -> 0-out.
 */
export function costForTokens(
  tokens:
    | number
    | null
    | undefined
    | { input?: number | null; output?: number | null },
  model: string | null | undefined
): number {
  const rate = rateFor(model);
  if (tokens === null || tokens === undefined) return 0;

  if (typeof tokens === "number") {
    if (!Number.isFinite(tokens) || tokens <= 0) return 0;
    const inputTokens = tokens * DEFAULT_INPUT_RATIO;
    const outputTokens = tokens * (1 - DEFAULT_INPUT_RATIO);
    return (
      (inputTokens / 1_000_000) * rate.inputPerMTokens +
      (outputTokens / 1_000_000) * rate.outputPerMTokens
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
