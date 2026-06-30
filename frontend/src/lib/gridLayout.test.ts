import { describe, expect, it } from "vitest";

import type { CardSummary } from "./api";
import type { RatesPayload } from "./cost";
import {
  axisValue,
  classifyQuadrant,
  defaultScaleFor,
  isStakes,
  normalize,
  projectColor,
  QUADRANT_LABEL,
  snapStakes,
  STAKES_ORDER,
  UNASSIGNED_COLOR,
} from "./gridLayout";

const RATES: RatesPayload = {
  rates: [
    {
      model: "claude-opus-4-7",
      displayName: "Opus 4.7",
      inputPerMTokens: 15,
      outputPerMTokens: 75,
    },
    {
      model: "claude-sonnet-4-6",
      displayName: "Sonnet 4.6",
      inputPerMTokens: 3,
      outputPerMTokens: 15,
    },
  ],
  defaultInputRatio: 0.6,
};

function makeCard(fm: Record<string, unknown>): CardSummary {
  return {
    id: typeof fm["id"] === "string" ? (fm["id"] as string) : "test-1",
    file: "/tmp/test-1.md",
    status: "backlog",
    frontmatter: fm,
    mtimeMs: 0,
  };
}

describe("isStakes", () => {
  it("accepts the three canonical values", () => {
    expect(isStakes("low")).toBe(true);
    expect(isStakes("medium")).toBe(true);
    expect(isStakes("high")).toBe(true);
  });
  it("rejects everything else", () => {
    expect(isStakes("HIGH")).toBe(false); // not pre-lowercased by isStakes
    expect(isStakes("urgent")).toBe(false);
    expect(isStakes(2)).toBe(false);
    expect(isStakes(null)).toBe(false);
    expect(isStakes(undefined)).toBe(false);
  });
});

describe("axisValue", () => {
  it("returns the estimated $ cost for the 'cost' axis", () => {
    const card = makeCard({
      id: "c1",
      model: "claude-sonnet-4-6",
      estimated_tokens: 1_000_000,
    });
    const v = axisValue(card, "cost", RATES);
    expect(v).not.toBeNull();
    // 1M tokens at sonnet ratesS, 60/40 blend: 0.6 * 3 + 0.4 * 15 = 7.8
    expect(v).toBeCloseTo(7.8, 5);
  });

  it("returns null for cost axis when no token data", () => {
    const card = makeCard({ id: "c1" });
    expect(axisValue(card, "cost", RATES)).toBeNull();
  });

  it("maps stakes to an ordinal index, case-insensitively", () => {
    expect(axisValue(makeCard({ stakes: "low" }), "stakes", RATES)).toBe(0);
    expect(axisValue(makeCard({ stakes: "MEDIUM" }), "stakes", RATES)).toBe(1);
    expect(axisValue(makeCard({ stakes: "High" }), "stakes", RATES)).toBe(2);
  });

  it("returns null for unknown stakes values", () => {
    expect(
      axisValue(makeCard({ stakes: "urgent" }), "stakes", RATES)
    ).toBeNull();
    expect(axisValue(makeCard({}), "stakes", RATES)).toBeNull();
  });

  it("returns the points number for the 'points' axis", () => {
    expect(axisValue(makeCard({ points: 3 }), "points", RATES)).toBe(3);
    expect(axisValue(makeCard({}), "points", RATES)).toBeNull();
  });

  it("tier currently mirrors points (no first-class tier field yet)", () => {
    expect(axisValue(makeCard({ points: 2 }), "tier", RATES)).toBe(2);
  });
});

describe("normalize", () => {
  it("returns all nulls when every input is null", () => {
    expect(normalize([null, null], "linear")).toEqual([null, null]);
  });

  it("linear: maps min to 0 and max to 1", () => {
    const out = normalize([0, 5, 10], "linear");
    expect(out).toEqual([0, 0.5, 1]);
  });

  it("linear: constant input collapses to 0.5", () => {
    const out = normalize([3, 3, 3], "linear");
    expect(out).toEqual([0.5, 0.5, 0.5]);
  });

  it("linear: preserves nulls in place", () => {
    const out = normalize([0, null, 10], "linear");
    expect(out).toEqual([0, null, 1]);
  });

  it("log: compresses long-tailed inputs more than linear", () => {
    // values: 0.1, 1, 10, 100  (4 orders of magnitude)
    const out = normalize([0.1, 1, 10, 100], "log");
    expect(out[0]).toBeCloseTo(0, 5);
    expect(out[3]).toBeCloseTo(1, 5);
    // The middle two should be reasonably spaced -- with log, 1 lands
    // far closer to 10 than linear would put it.
    expect(out[1]!).toBeGreaterThan(0.1);
    expect(out[1]!).toBeLessThan(out[2]!);
  });

  it("ordinal: spreads unique values evenly, regardless of magnitudes", () => {
    const out = normalize([1, 100, 10_000], "ordinal");
    expect(out).toEqual([0, 0.5, 1]);
  });

  it("ordinal: duplicates collapse to the same slot", () => {
    const out = normalize([2, 2, 5, 5, 9], "ordinal");
    // unique values: [2, 5, 9] -> ranks 0, 0.5, 1
    expect(out).toEqual([0, 0, 0.5, 0.5, 1]);
  });

  it("ordinal: single unique value maps to 0.5", () => {
    const out = normalize([7, 7, 7], "ordinal");
    expect(out).toEqual([0.5, 0.5, 0.5]);
  });

  it("ordinal: mixed nulls and real values preserves positions", () => {
    const out = normalize([null, 2, 5, null, 9], "ordinal");
    // unique reals: [2, 5, 9] -> 0, 0.5, 1
    expect(out).toEqual([null, 0, 0.5, null, 1]);
  });

  it("log: clamps negative inputs to 0 rather than NaN-ing the range", () => {
    // -5 should be treated as 0 under the log branch (clamped). The
    // function shouldn't produce NaN or Infinity.
    const out = normalize([-5, 10, 100], "log");
    expect(out.every((v) => v === null || Number.isFinite(v))).toBe(true);
    // -5 (clamped to 0) is the minimum after transform.
    expect(out[0]).toBe(0);
    expect(out[2]).toBe(1);
  });
});

describe("defaultScaleFor", () => {
  it("picks log for cost and ordinal for the rest", () => {
    expect(defaultScaleFor("cost")).toBe("log");
    expect(defaultScaleFor("stakes")).toBe("ordinal");
    expect(defaultScaleFor("points")).toBe("ordinal");
    expect(defaultScaleFor("tier")).toBe("ordinal");
  });
});

describe("classifyQuadrant", () => {
  it("classifies the four canonical corners", () => {
    expect(classifyQuadrant(0, 1)).toBe("priority"); // low cost, high value
    expect(classifyQuadrant(1, 1)).toBe("do-carefully");
    expect(classifyQuadrant(0, 0)).toBe("backlog");
    expect(classifyQuadrant(1, 0)).toBe("cancel");
  });

  it("the exact center sits on the high-y high-x boundary -> do-carefully", () => {
    expect(classifyQuadrant(0.5, 0.5)).toBe("do-carefully");
  });

  it("respects a custom cutoff", () => {
    expect(classifyQuadrant(0.3, 0.8, 0.4)).toBe("priority");
    expect(classifyQuadrant(0.5, 0.3, 0.4)).toBe("cancel");
  });

  it("every quadrant has a label", () => {
    for (const q of ["priority", "do-carefully", "backlog", "cancel"] as const) {
      expect(QUADRANT_LABEL[q].length).toBeGreaterThan(0);
    }
  });
});

describe("snapStakes", () => {
  it("clamps below 0 to low and above 1 to high", () => {
    expect(snapStakes(-1)).toBe("low");
    expect(snapStakes(2)).toBe("high");
  });

  it("rounds to nearest bucket", () => {
    // Three buckets at 0, 0.5, 1 in math-Y space.
    expect(snapStakes(0)).toBe("low");
    expect(snapStakes(0.2)).toBe("low");
    expect(snapStakes(0.3)).toBe("medium"); // closer to 0.5 than to 0
    expect(snapStakes(0.5)).toBe("medium");
    expect(snapStakes(0.7)).toBe("medium"); // closer to 0.5 than to 1
    expect(snapStakes(0.8)).toBe("high");
    expect(snapStakes(1)).toBe("high");
  });

  it("STAKES_ORDER is the canonical low->high ordering", () => {
    expect(STAKES_ORDER).toEqual(["low", "medium", "high"]);
  });

  it("0.25 sits on the low/medium boundary -- pins JS Math.round tie behavior", () => {
    // Math.round(0.25 * 2) = Math.round(0.5) = 1 in JS (rounds half away
    // from zero), so y=0.25 lands in medium. If a future JS change ever
    // alters this, the grid's tier-edit behavior at the 25%-line would
    // flip silently -- this test pins it.
    expect(snapStakes(0.25)).toBe("medium");
    // Symmetric upper boundary.
    expect(snapStakes(0.75)).toBe("high");
  });
});

describe("projectColor", () => {
  it("returns a fixed neutral color for Unassigned", () => {
    expect(projectColor("Unassigned")).toBe(UNASSIGNED_COLOR);
  });

  it("returns the same color for the same project name", () => {
    expect(projectColor("agile-cards-board")).toBe(
      projectColor("agile-cards-board")
    );
  });

  it("different projects can collide but a few common ones don't", () => {
    // Spot-check that the palette gives at least two distinct colors
    // across a handful of typical project names.
    const colors = new Set([
      projectColor("agile-cards-board"),
      projectColor("paradigm-portal"),
      projectColor("nexus"),
      projectColor("career-ops"),
    ]);
    expect(colors.size).toBeGreaterThan(1);
  });
});
