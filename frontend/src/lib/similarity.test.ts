import { describe, expect, it } from "vitest";

import { rankSimilar, titleSimilarity, titleTokens } from "./similarity";

describe("titleTokens", () => {
  it("lowercases, splits on spaces and dashes, drops stopwords and single chars", () => {
    const tokens = titleTokens("Add rate-limit middleware to the API");
    expect(tokens).toEqual(
      new Set(["add", "rate", "limit", "middleware", "api"])
    );
  });

  it("strips punctuation", () => {
    expect(titleTokens("Fix: cache (v2)!")).toEqual(
      new Set(["fix", "cache", "v2"])
    );
  });
});

describe("titleSimilarity", () => {
  it("is 1 for identical titles and 0 for disjoint ones", () => {
    expect(titleSimilarity("Add rate limit", "Add rate limit")).toBe(1);
    expect(titleSimilarity("Add rate limit", "Refactor docs build")).toBe(0);
  });

  it("flags rephrasings of the same work", () => {
    const s = titleSimilarity(
      "Add rate limiting middleware",
      "Rate limiting middleware for express"
    );
    expect(s).toBeGreaterThan(0.34);
  });

  it("is symmetric", () => {
    const a = "Add retry logic to fetch";
    const b = "Fetch retry handling";
    expect(titleSimilarity(a, b)).toBeCloseTo(titleSimilarity(b, a));
  });

  it("returns 0 when either side has no usable tokens", () => {
    expect(titleSimilarity("", "Add rate limit")).toBe(0);
    expect(titleSimilarity("the of a", "Add rate limit")).toBe(0);
  });
});

describe("rankSimilar", () => {
  const items = [
    { id: "c1", title: "Add rate limiting middleware" },
    { id: "c2", title: "Rate limit middleware v2" },
    { id: "c3", title: "Sprint retro notes UI" },
  ];

  it("returns matches above the threshold, best first, capped at limit", () => {
    const out = rankSimilar(
      "Add rate limit middleware",
      items,
      (i) => i.title
    );
    expect(out.map((s) => s.item.id)).toContain("c2");
    expect(out.map((s) => s.item.id)).not.toContain("c3");
    expect(out[0]!.similarity).toBeGreaterThanOrEqual(
      out[out.length - 1]!.similarity
    );
  });

  it("respects a custom limit", () => {
    const out = rankSimilar("rate limit middleware", items, (i) => i.title, {
      threshold: 0.1,
      limit: 1,
    });
    expect(out).toHaveLength(1);
  });
});
