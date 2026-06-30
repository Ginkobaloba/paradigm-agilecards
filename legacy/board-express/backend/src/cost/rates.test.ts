/**
 * Tests for the cost-rate helpers. node:test (via tsx) is the runner the
 * rest of the backend uses.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  costForTokens,
  DEFAULT_INPUT_RATIO,
  FALLBACK_RATE,
  MODEL_RATES,
  rateFor,
} from "./rates.js";

describe("rateFor", () => {
  it("returns a configured rate when the model is known", () => {
    const r = rateFor("claude-sonnet-4-6");
    assert.equal(r.model, "claude-sonnet-4-6");
    assert.equal(r.inputPerMTokens, 3);
  });

  it("falls back to FALLBACK_RATE for unknown models", () => {
    const r = rateFor("some-other-model");
    assert.equal(r.model, FALLBACK_RATE.model);
  });

  it("falls back for null/undefined/empty model strings", () => {
    assert.equal(rateFor(null).model, FALLBACK_RATE.model);
    assert.equal(rateFor(undefined).model, FALLBACK_RATE.model);
    assert.equal(rateFor("").model, FALLBACK_RATE.model);
  });
});

describe("costForTokens (aggregate)", () => {
  it("returns 0 for empty or invalid token counts", () => {
    assert.equal(costForTokens(0, "claude-sonnet-4-6"), 0);
    assert.equal(costForTokens(null, "claude-sonnet-4-6"), 0);
    assert.equal(costForTokens(undefined, "claude-sonnet-4-6"), 0);
    assert.equal(costForTokens(NaN, "claude-sonnet-4-6"), 0);
    assert.equal(costForTokens(-100, "claude-sonnet-4-6"), 0);
  });

  it("applies the default 60/40 input/output blend to an aggregate", () => {
    // 1M tokens at sonnet's 3 in / 15 out, blended 60/40:
    //   0.6 * 3 + 0.4 * 15 = 1.8 + 6 = 7.8
    const cost = costForTokens(1_000_000, "claude-sonnet-4-6");
    assert.equal(cost.toFixed(4), "7.8000");
    assert.equal(
      cost,
      DEFAULT_INPUT_RATIO * 3 + (1 - DEFAULT_INPUT_RATIO) * 15
    );
  });

  it("scales linearly with token count", () => {
    const a = costForTokens(500_000, "claude-haiku-4-5");
    const b = costForTokens(1_000_000, "claude-haiku-4-5");
    assert.equal(b.toFixed(6), (a * 2).toFixed(6));
  });
});

describe("costForTokens (split)", () => {
  it("uses the explicit input/output split when provided", () => {
    // Opus 4.7: 15 in, 75 out. 100k in + 50k out:
    //   (100000/1M)*15 + (50000/1M)*75 = 1.5 + 3.75 = 5.25
    const cost = costForTokens(
      { input: 100_000, output: 50_000 },
      "claude-opus-4-7"
    );
    assert.equal(cost.toFixed(4), "5.2500");
  });

  it("treats missing/invalid halves as zero", () => {
    const cost = costForTokens({ input: 100_000 }, "claude-opus-4-7");
    assert.equal(cost.toFixed(4), "1.5000");
    assert.equal(
      costForTokens({ output: null, input: NaN }, "claude-opus-4-7"),
      0
    );
  });
});

describe("MODEL_RATES table", () => {
  it("includes the four Claude tiers we currently dispatch to", () => {
    const ids = new Set(MODEL_RATES.map((r) => r.model));
    assert.ok(ids.has("claude-opus-4-7"), "expected opus-4-7");
    assert.ok(ids.has("claude-sonnet-4-6"), "expected sonnet-4-6");
    assert.ok(ids.has("claude-haiku-4-5"), "expected haiku-4-5");
  });
  it("never has a zero or negative rate", () => {
    for (const r of MODEL_RATES) {
      assert.ok(r.inputPerMTokens > 0, `${r.model} input rate must be > 0`);
      assert.ok(r.outputPerMTokens > 0, `${r.model} output rate must be > 0`);
    }
  });
});
