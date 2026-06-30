import { describe, it, expect } from "vitest";

import type { CardSummary } from "./api";
import {
  computeDepLayout,
  countDependents,
} from "./depGraph";

function card(id: string, dependsOn: string[] = []): CardSummary {
  return {
    id,
    file: `/c/${id}.md`,
    status: "backlog",
    frontmatter: { depends_on: dependsOn },
    mtimeMs: 0,
  };
}

describe("computeDepLayout", () => {
  it("empty input -> single empty column", () => {
    const layout = computeDepLayout([]);
    expect(layout.columns).toHaveLength(1);
    expect(layout.columns[0]).toEqual([]);
  });

  it("no deps -> all cards at depth 0", () => {
    const layout = computeDepLayout([card("a"), card("b"), card("c")]);
    expect(layout.columns).toHaveLength(1);
    expect(layout.columns[0]?.map((n) => n.card.id).sort()).toEqual(["a", "b", "c"]);
  });

  it("linear chain a -> b -> c gives depth 0,1,2", () => {
    const layout = computeDepLayout([
      card("c", ["b"]),
      card("b", ["a"]),
      card("a"),
    ]);
    expect(layout.nodes.get("a")?.depth).toBe(0);
    expect(layout.nodes.get("b")?.depth).toBe(1);
    expect(layout.nodes.get("c")?.depth).toBe(2);
    expect(layout.columns).toHaveLength(3);
  });

  it("diamond: a -> {b,c} -> d resolves d to depth 2", () => {
    const layout = computeDepLayout([
      card("a"),
      card("b", ["a"]),
      card("c", ["a"]),
      card("d", ["b", "c"]),
    ]);
    expect(layout.nodes.get("d")?.depth).toBe(2);
  });

  it("dep on a card outside the visible set lands in externalDeps and does not push depth", () => {
    const layout = computeDepLayout([
      card("a", ["ghost", "b"]),
      card("b"),
    ]);
    const a = layout.nodes.get("a");
    expect(a?.depth).toBe(1);
    expect(a?.visibleDeps).toEqual(["b"]);
    expect(a?.externalDeps).toEqual(["ghost"]);
  });

  it("self-loop is flagged as a cycle", () => {
    const layout = computeDepLayout([card("a", ["a"])]);
    expect(layout.nodes.get("a")?.inCycle).toBe(true);
    expect(layout.cycleIds.has("a")).toBe(true);
  });

  it("2-node cycle: a -> b -> a, both flagged", () => {
    const layout = computeDepLayout([card("a", ["b"]), card("b", ["a"])]);
    expect(layout.cycleIds.has("a")).toBe(true);
    expect(layout.cycleIds.has("b")).toBe(true);
  });

  it("isolated node not in a cycle is not flagged", () => {
    const layout = computeDepLayout([
      card("a", ["b"]),
      card("b", ["a"]),
      card("c"),
    ]);
    expect(layout.nodes.get("c")?.inCycle).toBe(false);
  });

  it("columns are sorted by id within a depth", () => {
    const layout = computeDepLayout([card("z"), card("a"), card("m")]);
    expect(layout.columns[0]?.map((n) => n.card.id)).toEqual(["a", "m", "z"]);
  });
});

describe("countDependents", () => {
  it("returns 0 for a leaf (no one depends on it)", () => {
    const layout = computeDepLayout([card("a"), card("b", ["a"])]);
    expect(countDependents("b", layout)).toBe(0);
  });

  it("counts direct dependents", () => {
    const layout = computeDepLayout([
      card("a"),
      card("b", ["a"]),
      card("c", ["a"]),
    ]);
    expect(countDependents("a", layout)).toBe(2);
  });

  it("counts transitive dependents", () => {
    const layout = computeDepLayout([
      card("a"),
      card("b", ["a"]),
      card("c", ["b"]),
      card("d", ["c"]),
    ]);
    expect(countDependents("a", layout)).toBe(3);
  });
});
