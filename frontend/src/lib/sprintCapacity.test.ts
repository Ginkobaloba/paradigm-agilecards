import { describe, it, expect } from "vitest";

import type {
  CardSummary,
  Sprint,
  SprintCardLink,
} from "./api";
import type { ModelRate } from "./cost";
import {
  REVIEW_MINUTES_PER_POINT,
  computeSprintCapacity,
} from "./sprintCapacity";

function card(
  id: string,
  fm: Record<string, unknown> = {}
): CardSummary {
  return {
    id,
    file: `/c/${id}.md`,
    status: "backlog",
    frontmatter: fm,
    mtimeMs: 0,
  };
}

const RATES: ModelRate[] = [
  {
    model: "claude-sonnet-4-6",
    inputPerMTokens: 3,
    outputPerMTokens: 15,
  },
];

function sprint(
  over: Partial<Pick<Sprint, "pointsTarget" | "dollarTarget" | "reviewHoursTarget">> = {}
): Pick<
  Sprint,
  "pointsTarget" | "dollarTarget" | "reviewHoursTarget"
> {
  return {
    pointsTarget: over.pointsTarget ?? null,
    dollarTarget: over.dollarTarget ?? null,
    reviewHoursTarget: over.reviewHoursTarget ?? null,
  };
}

function members(
  ...links: Array<{ cardId: string; plannedPoints?: number | null }>
): SprintCardLink[] {
  return links.map((l) => ({
    sprintId: 1,
    cardId: l.cardId,
    plannedPoints: l.plannedPoints ?? null,
  }));
}

describe("computeSprintCapacity", () => {
  it("returns level=none for every metric when no target is set", () => {
    const cap = computeSprintCapacity(sprint(), members(), {}, RATES);
    expect(cap.points.level).toBe("none");
    expect(cap.dollars.level).toBe("none");
    expect(cap.reviewHours.level).toBe("none");
    expect(cap.overall).toBe("none");
  });

  it("sums plannedPoints when present; falls back to frontmatter points", () => {
    const cards = {
      a: card("a", { points: 3 }),
      b: card("b", { points: 8 }),
      c: card("c", { points: 5 }),
    };
    const cap = computeSprintCapacity(
      sprint({ pointsTarget: 25 }),
      members(
        { cardId: "a", plannedPoints: 2 }, // override -> 2
        { cardId: "b" }, // fall back -> 8
        { cardId: "c", plannedPoints: null } // fall back -> 5
      ),
      cards,
      RATES
    );
    expect(cap.points.used).toBe(2 + 8 + 5);
    expect(cap.points.target).toBe(25);
    expect(cap.points.level).toBe("ok");
  });

  it("points level: 0-79% ok, 80-99% warn, >=100% over", () => {
    const cards = { a: card("a", { points: 8 }) };
    const m = members({ cardId: "a" });
    // target 10; used 8 -> 80% -> warn
    expect(
      computeSprintCapacity(
        sprint({ pointsTarget: 10 }),
        m,
        cards,
        RATES
      ).points.level
    ).toBe("warn");
    // target 11 -> 8/11 ~73% -> ok
    expect(
      computeSprintCapacity(
        sprint({ pointsTarget: 11 }),
        m,
        cards,
        RATES
      ).points.level
    ).toBe("ok");
    // target 8 -> 100% -> over
    expect(
      computeSprintCapacity(
        sprint({ pointsTarget: 8 }),
        m,
        cards,
        RATES
      ).points.level
    ).toBe("over");
  });

  it("sums dollar cost across member cards via the rate table", () => {
    const cards = {
      a: card("a", {
        model: "claude-sonnet-4-6",
        estimated_tokens: 1_000_000,
      }),
      b: card("b", {
        model: "claude-sonnet-4-6",
        estimated_tokens: 500_000,
      }),
    };
    const cap = computeSprintCapacity(
      sprint({ dollarTarget: 10 }),
      members({ cardId: "a" }, { cardId: "b" }),
      cards,
      RATES,
      0.6
    );
    // a: 600k in*3 + 400k out*15 = $1.80 + $6 = $7.80
    // b: 300k in*3 + 200k out*15 = $0.90 + $3 = $3.90
    // total $11.70 -> over $10
    expect(cap.dollars.used).toBeCloseTo(11.7, 2);
    expect(cap.dollars.level).toBe("over");
  });

  it("review hours uses the heuristic REVIEW_MINUTES_PER_POINT", () => {
    const cards = { a: card("a", { points: 12 }) };
    const cap = computeSprintCapacity(
      sprint({ reviewHoursTarget: 2 }),
      members({ cardId: "a" }),
      cards,
      RATES
    );
    // 12 points * 5 min = 60 min = 1 hour, target 2 -> 50% -> ok
    expect(cap.reviewHours.used).toBeCloseTo(
      (12 * REVIEW_MINUTES_PER_POINT) / 60,
      6
    );
    expect(cap.reviewHours.level).toBe("ok");
  });

  it("overall is the worst of the three levels", () => {
    const cards = {
      a: card("a", {
        points: 50,
        model: "claude-sonnet-4-6",
        estimated_tokens: 1_000,
      }),
    };
    const cap = computeSprintCapacity(
      sprint({
        pointsTarget: 25, // over
        dollarTarget: 100, // ok (very cheap)
        reviewHoursTarget: 10, // 50 * 5 / 60 = 4.17h / 10 -> ok
      }),
      members({ cardId: "a" }),
      cards,
      RATES
    );
    expect(cap.points.level).toBe("over");
    expect(cap.dollars.level).toBe("ok");
    expect(cap.reviewHours.level).toBe("ok");
    expect(cap.overall).toBe("over");
  });

  it("missing card in the index contributes 0 to all rollups", () => {
    const cap = computeSprintCapacity(
      sprint({ pointsTarget: 10, dollarTarget: 10 }),
      members({ cardId: "ghost", plannedPoints: 3 }),
      {},
      RATES
    );
    // plannedPoints honored without the card; cost is 0 because there's
    // no card to read tokens from.
    expect(cap.points.used).toBe(3);
    expect(cap.dollars.used).toBe(0);
  });

  it("memberCount mirrors input length", () => {
    const cards = { a: card("a"), b: card("b") };
    const cap = computeSprintCapacity(
      sprint(),
      members({ cardId: "a" }, { cardId: "b" }),
      cards,
      RATES
    );
    expect(cap.memberCount).toBe(2);
  });
});
