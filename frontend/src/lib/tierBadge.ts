/**
 * Badge styling helpers. They map card metadata -- tier, stakes, and
 * column status -- to Tailwind classes so the components stay
 * declarative and the palette lives in one place.
 */

const TIER_CLASSES: Record<number, string> = {
  1: "bg-tier-1",
  2: "bg-tier-2",
  3: "bg-tier-3",
  4: "bg-tier-4",
  5: "bg-tier-5",
  6: "bg-tier-6",
};

/** Tier number (1-6) -> badge background class. */
export function tierBadgeClass(tier: number | null | undefined): string {
  if (typeof tier !== "number") return "bg-panel2";
  return TIER_CLASSES[tier] ?? "bg-panel2";
}

/**
 * Stakes -> outlined-pill classes. Risk reads as a colour gradient:
 * low is neutral, medium amber, high red.
 */
export function stakesBadgeClass(stakes: string | null | undefined): string {
  switch (stakes) {
    case "high":
      return "text-danger border-danger/40 bg-danger/10";
    case "medium":
      return "text-warn border-warn/40 bg-warn/10";
    case "low":
    default:
      return "text-muted border-border bg-panel";
  }
}

/**
 * Column status -> accent-dot colour, so a column is findable by its
 * colour without reading the label.
 */
export function statusDotClass(status: string): string {
  switch (status) {
    case "active":
      return "bg-accent";
    case "awaiting_amendment_review":
      return "bg-warn";
    case "done":
      return "bg-ok";
    case "blocked":
      return "bg-danger";
    case "backlog":
    default:
      return "bg-muted";
  }
}

export function tierLabel(
  fm: Record<string, unknown>
): { tier: number | null; model: string | null } {
  const tier =
    typeof fm["points"] === "number" ? (fm["points"] as number) : null;
  const model = typeof fm["model"] === "string" ? (fm["model"] as string) : null;
  return { tier, model };
}
