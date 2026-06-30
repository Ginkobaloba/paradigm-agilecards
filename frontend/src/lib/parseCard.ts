/**
 * Helpers for extracting human-friendly fields off a card's frontmatter
 * without sprinkling `as string` checks all over the components.
 */

import type { CardSummary } from "./api";

export function cardTitle(c: CardSummary): string {
  const t = c.frontmatter["title"];
  if (typeof t === "string" && t.length > 0) return t;
  return c.id;
}

/**
 * Short id for dense surfaces -- the `<batch>-<NN>` prefix without the
 * verb-noun slug. "b042-05-document-cascade-routing" -> "b042-05". The
 * full id still shows in the card modal.
 */
export function cardShortId(c: CardSummary): string {
  const parts = c.id.split("-");
  if (parts.length >= 2) return `${parts[0]}-${parts[1]}`;
  return c.id;
}

export function cardBatch(c: CardSummary): string | null {
  const b = c.frontmatter["batch"];
  return typeof b === "string" ? b : null;
}

export function cardProject(c: CardSummary): string | null {
  const p = c.frontmatter["project"];
  return typeof p === "string" ? p : null;
}

export function cardStakes(c: CardSummary): string | null {
  const s = c.frontmatter["stakes"];
  return typeof s === "string" ? s : null;
}

export function cardPoints(c: CardSummary): number | null {
  const p = c.frontmatter["points"];
  return typeof p === "number" ? p : null;
}

export function cardExtendedThinking(c: CardSummary): boolean {
  return c.frontmatter["extended_thinking"] === true;
}

export function cardModel(c: CardSummary): string | null {
  const m = c.frontmatter["model"];
  return typeof m === "string" ? m : null;
}

export function cardPinRequired(c: CardSummary): boolean {
  return c.frontmatter["pin_required"] === true;
}

/**
 * The grooming "ready for sprint" label. Distinct from `status:` -- a
 * card stays in the backlog folder whether or not it is marked ready;
 * `ready` only splits the ice-box from the sprint-ready set in the
 * backlog grooming surface.
 */
export function cardReady(c: CardSummary): boolean {
  return c.frontmatter["ready"] === true;
}

export function cardDependsOn(c: CardSummary): string[] {
  const d = c.frontmatter["depends_on"];
  if (Array.isArray(d)) return d.filter((x): x is string => typeof x === "string");
  return [];
}

export function cardEstimatedTokens(c: CardSummary): number | null {
  const v = c.frontmatter["estimated_tokens"];
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

export function cardActualTokens(c: CardSummary): number | null {
  const v = c.frontmatter["actual_tokens"];
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

export function cardCostCap(c: CardSummary): number | null {
  const v = c.frontmatter["cost_cap_usd"];
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

export function cardClaimedBy(c: CardSummary): string | null {
  const v = c.frontmatter["claimed_by"];
  return typeof v === "string" && v.length > 0 ? v : null;
}
