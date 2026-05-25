import { describe, expect, it } from "vitest";

import { fuzzyRank, fuzzyScore } from "./fuzzy";

describe("fuzzyScore", () => {
  it("empty query matches everything with score 0", () => {
    expect(fuzzyScore("", "anything").score).toBe(0);
  });

  it("substring beats subsequence", () => {
    const sub = fuzzyScore("runner", "wire the runner loop").score;
    const sub2 = fuzzyScore("runner", "rxuxnxnxexr loop").score;
    expect(sub).toBeGreaterThan(sub2);
  });

  it("returns -1 when no subsequence match", () => {
    expect(fuzzyScore("xyz", "abc").score).toBe(-1);
  });

  it("returns the matched indices for substring hits", () => {
    const r = fuzzyScore("run", "the-runner-loop");
    expect(r.indices).toEqual([4, 5, 6]);
  });

  it("is case-insensitive", () => {
    expect(fuzzyScore("RUN", "runner").score).toBeGreaterThan(0);
  });
});

describe("fuzzyRank", () => {
  it("orders matches by score and respects the limit", () => {
    const items = [
      "b042-runner-claim",
      "wire-runner-claim",
      "b044-async-thing",
      "the-runner",
      "totally unrelated",
    ];
    const ranked = fuzzyRank("runner", items, (s) => s, 3);
    expect(ranked).toHaveLength(3);
    // "the-runner" is shorter, should win
    expect(ranked[0]?.item).toBe("the-runner");
    expect(ranked.every((r) => r.item.includes("runner"))).toBe(true);
  });

  it("filters out non-matches", () => {
    const items = ["alpha", "beta", "gamma"];
    expect(fuzzyRank("xyz", items, (s) => s).length).toBe(0);
  });
});
