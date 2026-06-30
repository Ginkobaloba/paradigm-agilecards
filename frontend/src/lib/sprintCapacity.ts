/**
 * Capacity model for a sprint -- pure compute.
 *
 * Three constraints, each with its own target and "used" rollup over
 * the sprint's member cards:
 *
 *   - **points**: sum of plannedPoints (or each card's points frontmatter
 *     if no override was stored).
 *   - **dollars**: sum of the cost estimate of each member card,
 *     computed from the same rate table the kanban tiles use
 *     (lib/cost.ts cardCost).
 *   - **review hours**: heuristic at points * REVIEW_MINUTES_PER_POINT,
 *     converted to hours. Per-card review minutes is not a first-class
 *     frontmatter field today; the heuristic stays a single constant
 *     here until the runner / triage emits something better.
 *
 * Each metric resolves to one of:
 *   - `none`  -- target is null/unset, no opinion
 *   - `ok`    -- 0 <= used < 80% of target
 *   - `warn`  -- 80% <= used < 100%
 *   - `over`  -- used >= 100%
 *
 * The sprint-level stoplight is the worst of the three (over > warn >
 * ok > none).
 */

import type { CardSummary, Sprint, SprintCardLink } from "./api";

import { cardCost, type ModelRate } from "./cost";
import { cardPoints } from "./parseCard";

export type CapacityLevel = "none" | "ok" | "warn" | "over";

export interface CapacityMetric {
  /** "used" rollup in this metric's natural unit. */
  used: number;
  /** Target as stored on the sprint. null means "no target set". */
  target: number | null;
  /** Ratio used/target. 0 when target is null. */
  ratio: number;
  level: CapacityLevel;
}

export interface SprintCapacity {
  points: CapacityMetric;
  dollars: CapacityMetric;
  reviewHours: CapacityMetric;
  /** worst level across the three metrics. */
  overall: CapacityLevel;
  memberCount: number;
}

/** How many minutes of human review one point is assumed to cost. */
export const REVIEW_MINUTES_PER_POINT = 5;

function levelFor(used: number, target: number | null): CapacityLevel {
  if (target === null || !Number.isFinite(target) || target <= 0) return "none";
  const r = used / target;
  if (r >= 1) return "over";
  if (r >= 0.8) return "warn";
  return "ok";
}

function ratioFor(used: number, target: number | null): number {
  if (target === null || !Number.isFinite(target) || target <= 0) return 0;
  return used / target;
}

const LEVEL_RANK: Record<CapacityLevel, number> = {
  none: 0,
  ok: 1,
  warn: 2,
  over: 3,
};

function worst(...levels: CapacityLevel[]): CapacityLevel {
  let bestRank = -1;
  let bestLevel: CapacityLevel = "none";
  for (const l of levels) {
    if (LEVEL_RANK[l] > bestRank) {
      bestRank = LEVEL_RANK[l];
      bestLevel = l;
    }
  }
  return bestLevel;
}

export function computeSprintCapacity(
  sprint: Pick<
    Sprint,
    "pointsTarget" | "dollarTarget" | "reviewHoursTarget"
  >,
  members: readonly SprintCardLink[],
  cards: Readonly<Record<string, CardSummary>>,
  rates: readonly ModelRate[],
  defaultInputRatio?: number
): SprintCapacity {
  let pointsUsed = 0;
  let dollarsUsed = 0;

  for (const m of members) {
    const card = cards[m.cardId];
    const pp =
      typeof m.plannedPoints === "number" && Number.isFinite(m.plannedPoints)
        ? m.plannedPoints
        : (card ? cardPoints(card) ?? 0 : 0);
    pointsUsed += pp;
    if (card) {
      const c = cardCost(card, rates, defaultInputRatio);
      dollarsUsed += c.usd;
    }
  }

  const reviewHoursUsed = (pointsUsed * REVIEW_MINUTES_PER_POINT) / 60;

  const points: CapacityMetric = {
    used: pointsUsed,
    target: sprint.pointsTarget,
    ratio: ratioFor(pointsUsed, sprint.pointsTarget),
    level: levelFor(pointsUsed, sprint.pointsTarget),
  };
  const dollars: CapacityMetric = {
    used: dollarsUsed,
    target: sprint.dollarTarget,
    ratio: ratioFor(dollarsUsed, sprint.dollarTarget),
    level: levelFor(dollarsUsed, sprint.dollarTarget),
  };
  const reviewHours: CapacityMetric = {
    used: reviewHoursUsed,
    target: sprint.reviewHoursTarget,
    ratio: ratioFor(reviewHoursUsed, sprint.reviewHoursTarget),
    level: levelFor(reviewHoursUsed, sprint.reviewHoursTarget),
  };

  return {
    points,
    dollars,
    reviewHours,
    overall: worst(points.level, dollars.level, reviewHours.level),
    memberCount: members.length,
  };
}
