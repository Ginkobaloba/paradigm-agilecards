import { describe, expect, it, beforeEach } from "vitest";

import type { CardSummary } from "../lib/api";
import { selectCardsByStatus, selectUnmetDeps, useStore } from "./store";

function mkCard(
  id: string,
  status: CardSummary["status"] = "backlog",
  fm: Record<string, unknown> = {}
): CardSummary {
  return {
    id,
    file: `/tmp/${id}.md`,
    status,
    frontmatter: { title: id, ...fm },
    mtimeMs: Date.now(),
  };
}

describe("selectCardsByStatus", () => {
  beforeEach(() => {
    useStore.setState({
      cards: {},
      ranks: {},
      inFlight: new Set<string>(),
      hydrated: false,
    });
  });

  it("falls back to id sort when no ranks are set", () => {
    useStore.getState().setAll([
      mkCard("b042-03"),
      mkCard("b042-01"),
      mkCard("b042-02"),
    ]);
    const sorted = selectCardsByStatus(useStore.getState(), "backlog");
    expect(sorted.map((c) => c.id)).toEqual([
      "b042-01",
      "b042-02",
      "b042-03",
    ]);
  });

  it("respects persisted ranks in ascending order", () => {
    useStore.getState().setAll([
      mkCard("a"),
      mkCard("b"),
      mkCard("c"),
    ]);
    useStore.getState().setAllRanks([
      { cardId: "a", status: "backlog", rank: 3000 },
      { cardId: "b", status: "backlog", rank: 1000 },
      { cardId: "c", status: "backlog", rank: 2000 },
    ]);
    const sorted = selectCardsByStatus(useStore.getState(), "backlog");
    expect(sorted.map((c) => c.id)).toEqual(["b", "c", "a"]);
  });

  it("places unranked cards after ranked ones, sorted by id", () => {
    useStore.getState().setAll([
      mkCard("ranked-2"),
      mkCard("z-unranked"),
      mkCard("ranked-1"),
      mkCard("a-unranked"),
    ]);
    useStore.getState().setAllRanks([
      { cardId: "ranked-1", status: "backlog", rank: 100 },
      { cardId: "ranked-2", status: "backlog", rank: 200 },
    ]);
    const sorted = selectCardsByStatus(useStore.getState(), "backlog");
    expect(sorted.map((c) => c.id)).toEqual([
      "ranked-1",
      "ranked-2",
      "a-unranked",
      "z-unranked",
    ]);
  });
});

describe("selectUnmetDeps", () => {
  beforeEach(() => {
    useStore.setState({
      cards: {},
      ranks: {},
      inFlight: new Set<string>(),
      hydrated: false,
    });
  });

  it("reports zero for cards without deps", () => {
    const card = mkCard("c");
    useStore.getState().setAll([card]);
    const r = selectUnmetDeps(useStore.getState(), card);
    expect(r.count).toBe(0);
    expect(r.firstUnmetId).toBeNull();
  });

  it("counts deps whose target is not done", () => {
    const target = mkCard("dep1", "active");
    const card = mkCard("c", "backlog", { depends_on: ["dep1"] });
    useStore.getState().setAll([target, card]);
    const r = selectUnmetDeps(useStore.getState(), card);
    expect(r.count).toBe(1);
    expect(r.firstUnmetId).toBe("dep1");
  });

  it("does not count deps whose target is done", () => {
    const target = mkCard("dep1", "done");
    const card = mkCard("c", "backlog", { depends_on: ["dep1"] });
    useStore.getState().setAll([target, card]);
    expect(selectUnmetDeps(useStore.getState(), card).count).toBe(0);
  });

  it("ignores unknown dep ids (treats them as already-resolved)", () => {
    const card = mkCard("c", "backlog", { depends_on: ["ghost"] });
    useStore.getState().setAll([card]);
    expect(selectUnmetDeps(useStore.getState(), card).count).toBe(0);
  });

  it("returns the first unmet dep id in input order", () => {
    const d1 = mkCard("d1", "done");
    const d2 = mkCard("d2", "active");
    const d3 = mkCard("d3", "backlog");
    const card = mkCard("c", "backlog", { depends_on: ["d1", "d2", "d3"] });
    useStore.getState().setAll([d1, d2, d3, card]);
    const r = selectUnmetDeps(useStore.getState(), card);
    expect(r.count).toBe(2);
    expect(r.firstUnmetId).toBe("d2");
  });
});
