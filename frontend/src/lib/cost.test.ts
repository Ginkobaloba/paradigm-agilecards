import { describe, expect, it } from "vitest";

import type { CardSummary } from "./api";
import {
  cardCost,
  costForTokens,
  costLevel,
  formatCost,
  type ModelRate,
  rollupCost,
} from "./cost";

const RATES: ModelRate[] = [
  {
    model: "claude-opus-4-7",
    inputPerMTokens: 15,
    outputPerMTokens: 75,
  },
  {
    model: "claude-sonnet-4-6",
    inputPerMTokens: 3,
    outputPerMTokens: 15,
  },
];

function mkCard(fm: Record<string, unknown>): CardSummary {
  return {
    id: "test-card",
    file: "/tmp/test-card.md",
    status: "backlog",
    frontmatter: fm,
    mtimeMs: Date.now(),
  };
}

describe("costForTokens", () => {
  it("uses 60/40 default blend on aggregate counts", () => {
    // sonnet: 0.6*3 + 0.4*15 = 7.8 per 1M aggregate tokens
    expect(costForTokens(1_000_000, "claude-sonnet-4-6", RATES)).toBeCloseTo(
      7.8
    );
  });
  it("uses split when given input/output object", () => {
    expect(
      costForTokens({ input: 200_000, output: 100_000 }, "claude-opus-4-7", RATES)
    ).toBeCloseTo(0.2 * 15 + 0.1 * 75);
  });
  it("zero for empty / invalid", () => {
    expect(costForTokens(null, "claude-opus-4-7", RATES)).toBe(0);
    expect(costForTokens(0, "claude-opus-4-7", RATES)).toBe(0);
    expect(costForTokens(-100, "claude-opus-4-7", RATES)).toBe(0);
  });
});

describe("formatCost", () => {
  it("formats cents under a dollar", () => {
    expect(formatCost(0.12)).toBe("$0.12");
  });
  it("clamps tiny amounts so the chip doesn't show $0.00", () => {
    expect(formatCost(0.001)).toBe("<$0.01");
  });
  it("formats dollars under 100", () => {
    expect(formatCost(7.89)).toBe("$7.89");
  });
  it("formats dollars 100-999", () => {
    expect(formatCost(345)).toBe("$345");
  });
  it("k-suffixes thousands", () => {
    expect(formatCost(1234)).toBe("$1.2k");
  });
  it("zero / invalid returns $0", () => {
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(NaN)).toBe("$0");
  });
});

describe("costLevel", () => {
  it("ok with no cap", () => {
    expect(costLevel(50, null)).toBe("ok");
    expect(costLevel(50, 0)).toBe("ok");
  });
  it("ok under 80% of cap", () => {
    expect(costLevel(7.9, 10)).toBe("ok");
  });
  it("warn at 80% of cap", () => {
    expect(costLevel(8, 10)).toBe("warn");
  });
  it("danger at and above 100% of cap", () => {
    expect(costLevel(10, 10)).toBe("danger");
    expect(costLevel(99, 10)).toBe("danger");
  });
});

describe("cardCost", () => {
  it("prefers actual over estimate when both are present", () => {
    const c = mkCard({
      model: "claude-sonnet-4-6",
      estimated_tokens: 100_000,
      actual_tokens: 200_000,
    });
    const cc = cardCost(c, RATES);
    expect(cc.kind).toBe("spent");
    expect(cc.usd).toBeCloseTo(7.8 * 0.2); // 200k of 1M at sonnet blend
  });
  it("uses model_used over planned model when actuals are present", () => {
    const c = mkCard({
      model: "claude-opus-4-7",
      model_used: "claude-sonnet-4-6",
      actual_tokens: 1_000_000,
    });
    const cc = cardCost(c, RATES);
    expect(cc.model).toBe("claude-sonnet-4-6");
    expect(cc.usd).toBeCloseTo(7.8);
  });
  it("returns kind=none and zero when there is no token data", () => {
    expect(cardCost(mkCard({}), RATES).kind).toBe("none");
    expect(cardCost(mkCard({}), RATES).usd).toBe(0);
  });
  it("rolls level danger when spent crosses cap", () => {
    const c = mkCard({
      model: "claude-opus-4-7",
      actual_tokens: 1_000_000,
      cost_cap_usd: 10,
    });
    const cc = cardCost(c, RATES);
    expect(cc.level).toBe("danger");
  });
});

describe("rollupCost", () => {
  it("sums estimates only -> kind est", () => {
    const cards = [
      mkCard({ model: "claude-sonnet-4-6", estimated_tokens: 1_000_000 }),
      mkCard({ model: "claude-sonnet-4-6", estimated_tokens: 1_000_000 }),
    ];
    const r = rollupCost(cards, RATES);
    expect(r.kind).toBe("est");
    expect(r.usd).toBeCloseTo(15.6);
  });
  it("kind=mixed when both estimate and spent cards are present", () => {
    const cards = [
      mkCard({ model: "claude-sonnet-4-6", estimated_tokens: 500_000 }),
      mkCard({ model: "claude-sonnet-4-6", actual_tokens: 500_000 }),
    ];
    expect(rollupCost(cards, RATES).kind).toBe("mixed");
  });
  it("kind=none when no card has tokens", () => {
    expect(rollupCost([mkCard({})], RATES).kind).toBe("none");
  });
});
