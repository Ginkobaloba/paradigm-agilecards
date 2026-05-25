import { describe, expect, it } from "vitest";

import type { CardSummary } from "../lib/api";
import {
  activeFilterCount,
  cardMatchesFilters,
  chipOptions,
  EMPTY_FILTERS,
  filtersFromParams,
  filtersToParams,
  type FilterState,
} from "./filters";

function mkCard(
  id: string,
  fm: Record<string, unknown> = {}
): CardSummary {
  return {
    id,
    file: `/tmp/${id}.md`,
    status: "backlog",
    frontmatter: { title: id, ...fm },
    mtimeMs: Date.now(),
  };
}

const F = (over: Partial<FilterState> = {}): FilterState => ({
  ...EMPTY_FILTERS,
  ...over,
});

describe("cardMatchesFilters", () => {
  it("returns true for empty filters", () => {
    expect(cardMatchesFilters(mkCard("c"), F())).toBe(true);
  });

  it("matches title and id substrings (case-insensitive)", () => {
    const c = mkCard("b042-01-runner-claim", { title: "Wire runner heartbeat" });
    expect(cardMatchesFilters(c, F({ search: "HEART" }))).toBe(true);
    expect(cardMatchesFilters(c, F({ search: "b042" }))).toBe(true);
    expect(cardMatchesFilters(c, F({ search: "ghost" }))).toBe(false);
  });

  it("intersects across chips, unions within a chip", () => {
    const c = mkCard("c", { project: "acme", points: 4 });
    expect(
      cardMatchesFilters(c, F({ project: ["acme", "beta"], tier: [4] }))
    ).toBe(true);
    expect(
      cardMatchesFilters(c, F({ project: ["beta"], tier: [4] }))
    ).toBe(false);
    expect(
      cardMatchesFilters(c, F({ project: ["acme"], tier: [5] }))
    ).toBe(false);
  });

  it("tri-state pinRequired", () => {
    const pinned = mkCard("c", { pin_required: true });
    const unpinned = mkCard("c2", {});
    expect(cardMatchesFilters(pinned, F({ pinRequired: true }))).toBe(true);
    expect(cardMatchesFilters(unpinned, F({ pinRequired: true }))).toBe(false);
    expect(cardMatchesFilters(pinned, F({ pinRequired: null }))).toBe(true);
  });

  it("merge_status filter", () => {
    const m = mkCard("c", { merge_status: "merged" });
    expect(cardMatchesFilters(m, F({ mergeStatus: ["merged"] }))).toBe(true);
    expect(cardMatchesFilters(m, F({ mergeStatus: ["pending"] }))).toBe(false);
  });
});

describe("chipOptions", () => {
  it("collects unique values per chip from the card set", () => {
    const cards = [
      mkCard("c1", {
        project: "acme",
        batch: "b042",
        claimed_by: "runner-1",
        points: 4,
        stakes: "high",
        merge_status: "pending",
      }),
      mkCard("c2", {
        project: "beta",
        batch: "b042",
        claimed_by: "runner-2",
        points: 3,
        stakes: "low",
      }),
      mkCard("c3", { project: "acme", points: 4 }),
    ];
    const opts = chipOptions(cards);
    expect(opts.project).toEqual(["acme", "beta"]);
    expect(opts.batch).toEqual(["b042"]);
    expect(opts.claimedBy).toEqual(["runner-1", "runner-2"]);
    expect(opts.tier).toEqual([3, 4]);
    expect(opts.stakes).toEqual(["high", "low"]);
    expect(opts.mergeStatus).toEqual(["pending"]);
  });
});

describe("activeFilterCount", () => {
  it("counts each non-empty chip", () => {
    expect(activeFilterCount(F())).toBe(0);
    expect(activeFilterCount(F({ search: "x" }))).toBe(1);
    expect(
      activeFilterCount(F({ search: "x", tier: [4], pinRequired: true }))
    ).toBe(3);
  });
});

describe("URL codec", () => {
  it("round-trips through URLSearchParams", () => {
    const start: FilterState = F({
      search: "runner heartbeat",
      project: ["acme", "beta"],
      tier: [4, 5],
      pinRequired: true,
      extendedThinking: false,
      mergeStatus: ["merged"],
    });
    const params = filtersToParams(start);
    const back = filtersFromParams(params);
    expect(back).toEqual(start);
  });

  it("ignores unknown params", () => {
    const params = new URLSearchParams("foo=bar&tier=4");
    const back = filtersFromParams(params);
    expect(back.tier).toEqual([4]);
    expect(back.search).toBe("");
  });

  it("drops invalid tier values", () => {
    const back = filtersFromParams(new URLSearchParams("tier=4,abc,2"));
    expect(back.tier).toEqual([4, 2]);
  });
});
