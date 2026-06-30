import { describe, it, expect } from "vitest";

import type { CardSummary } from "../lib/api";
import {
  UNASSIGNED_PROJECT,
  partitionByProject,
  projectKeyOf,
} from "./lens";

function mkCard(over: Partial<CardSummary> & { id: string }): CardSummary {
  const { frontmatter, ...rest } = over;
  return {
    file: `/cards/${over.id}.md`,
    status: "backlog",
    mtimeMs: 0,
    ...rest,
    frontmatter: { ...(frontmatter ?? {}) },
  };
}

describe("projectKeyOf", () => {
  it("returns the project frontmatter string", () => {
    const c = mkCard({ id: "c1", frontmatter: { project: "agile-cards" } });
    expect(projectKeyOf(c)).toBe("agile-cards");
  });

  it("falls back to Unassigned when project is missing", () => {
    const c = mkCard({ id: "c1" });
    expect(projectKeyOf(c)).toBe(UNASSIGNED_PROJECT);
  });

  it("treats a non-string project as Unassigned", () => {
    const c = mkCard({ id: "c1", frontmatter: { project: 42 } });
    expect(projectKeyOf(c)).toBe(UNASSIGNED_PROJECT);
  });
});

describe("partitionByProject", () => {
  it("returns empty array for empty input", () => {
    expect(partitionByProject([])).toEqual([]);
  });

  it("groups cards by project, preserving order within each group", () => {
    const cards = [
      mkCard({ id: "a", frontmatter: { project: "x" } }),
      mkCard({ id: "b", frontmatter: { project: "y" } }),
      mkCard({ id: "c", frontmatter: { project: "x" } }),
      mkCard({ id: "d", frontmatter: { project: "y" } }),
    ];
    const groups = partitionByProject(cards);
    expect(groups.map((g) => g.key)).toEqual(["x", "y"]);
    expect(groups[0]?.cards.map((c) => c.id)).toEqual(["a", "c"]);
    expect(groups[1]?.cards.map((c) => c.id)).toEqual(["b", "d"]);
  });

  it("Unassigned sorts to the end even if it appeared first in input", () => {
    const cards = [
      mkCard({ id: "a" }), // no project -> Unassigned
      mkCard({ id: "b", frontmatter: { project: "x" } }),
      mkCard({ id: "c", frontmatter: { project: "y" } }),
      mkCard({ id: "d" }),
    ];
    const groups = partitionByProject(cards);
    expect(groups.map((g) => g.key)).toEqual(["x", "y", UNASSIGNED_PROJECT]);
    expect(groups[2]?.cards.map((c) => c.id)).toEqual(["a", "d"]);
  });

  it("treats empty-string project as Unassigned", () => {
    const cards = [mkCard({ id: "a", frontmatter: { project: "" } })];
    const groups = partitionByProject(cards);
    expect(groups.map((g) => g.key)).toEqual([UNASSIGNED_PROJECT]);
  });
});
