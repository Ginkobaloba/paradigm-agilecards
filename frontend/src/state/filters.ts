/**
 * Filter state for the board. A filter is the intersection of all
 * active chips; within a single chip, multi-selected values are union.
 *
 * State lives in its own zustand store so the kanban store stays
 * focused on cards/ranks. Filters URL-sync via separate hooks so the
 * store doesn't need to know about the address bar.
 */

import { create } from "zustand";

import type { CardSummary } from "../lib/api";
import {
  cardBatch,
  cardDependsOn,
  cardExtendedThinking,
  cardPinRequired,
  cardPoints,
  cardProject,
  cardStakes,
  cardTitle,
  cardClaimedBy,
} from "../lib/parseCard";

export interface FilterState {
  search: string;
  project: string[];
  batch: string[];
  claimedBy: string[];
  tier: number[];
  stakes: string[];
  pinRequired: boolean | null;
  extendedThinking: boolean | null;
  mergeStatus: string[];
}

export const EMPTY_FILTERS: FilterState = {
  search: "",
  project: [],
  batch: [],
  claimedBy: [],
  tier: [],
  stakes: [],
  pinRequired: null,
  extendedThinking: null,
  mergeStatus: [],
};

interface FilterStoreShape extends FilterState {
  setAll: (next: FilterState) => void;
  setSearch: (s: string) => void;
  toggleMulti: (key: MultiKey, value: MultiValueOf<MultiKey>) => void;
  setTri: (key: TriKey, value: boolean | null) => void;
  clearKey: (key: keyof FilterState) => void;
  reset: () => void;
}

type MultiKey =
  | "project"
  | "batch"
  | "claimedBy"
  | "tier"
  | "stakes"
  | "mergeStatus";

type MultiValueOf<K extends MultiKey> = K extends "tier" ? number : string;

type TriKey = "pinRequired" | "extendedThinking";

export const useFilters = create<FilterStoreShape>((set) => ({
  ...EMPTY_FILTERS,

  setAll: (next) => set(() => ({ ...next })),
  setSearch: (search) => set(() => ({ search })),
  toggleMulti: (key, value) =>
    set((s) => {
      // The key/value type relationship is enforced at the call site;
      // inside the store we treat both as unknowns and cast back when
      // writing.
      const list = (s[key] as unknown[]).slice();
      const idx = list.indexOf(value as unknown);
      if (idx >= 0) list.splice(idx, 1);
      else list.push(value as unknown);
      return { [key]: list } as unknown as Partial<FilterStoreShape>;
    }),
  setTri: (key, value) =>
    set(() => ({ [key]: value }) as unknown as Partial<FilterStoreShape>),
  clearKey: (key) =>
    set(() => {
      const empty = EMPTY_FILTERS[key];
      return { [key]: empty } as unknown as Partial<FilterStoreShape>;
    }),
  reset: () => set(() => ({ ...EMPTY_FILTERS })),
}));

/**
 * Pure predicate: does `card` pass `f`? Used by the kanban selector
 * after rank-sort.
 */
export function cardMatchesFilters(
  card: CardSummary,
  f: FilterState
): boolean {
  if (f.search.length > 0) {
    const needle = f.search.toLowerCase();
    const hay = `${cardTitle(card)} ${card.id}`.toLowerCase();
    if (!hay.includes(needle)) return false;
  }
  if (f.project.length > 0) {
    const p = cardProject(card);
    if (!p || !f.project.includes(p)) return false;
  }
  if (f.batch.length > 0) {
    const b = cardBatch(card);
    if (!b || !f.batch.includes(b)) return false;
  }
  if (f.claimedBy.length > 0) {
    const c = cardClaimedBy(card);
    if (!c || !f.claimedBy.includes(c)) return false;
  }
  if (f.tier.length > 0) {
    const t = cardPoints(card);
    if (typeof t !== "number" || !f.tier.includes(t)) return false;
  }
  if (f.stakes.length > 0) {
    const s = cardStakes(card);
    if (!s || !f.stakes.includes(s)) return false;
  }
  if (f.pinRequired !== null) {
    if (cardPinRequired(card) !== f.pinRequired) return false;
  }
  if (f.extendedThinking !== null) {
    if (cardExtendedThinking(card) !== f.extendedThinking) return false;
  }
  if (f.mergeStatus.length > 0) {
    const ms = card.frontmatter["merge_status"];
    if (typeof ms !== "string" || !f.mergeStatus.includes(ms)) return false;
  }
  return true;
}

/**
 * Surveys the current cards to produce the set of values each
 * multi-select chip can offer. Driven from the live data so a chip
 * never shows a value that doesn't exist on any card.
 */
export interface ChipOptions {
  project: string[];
  batch: string[];
  claimedBy: string[];
  tier: number[];
  stakes: string[];
  mergeStatus: string[];
}

export function chipOptions(cards: readonly CardSummary[]): ChipOptions {
  const project = new Set<string>();
  const batch = new Set<string>();
  const claimedBy = new Set<string>();
  const tier = new Set<number>();
  const stakes = new Set<string>();
  const mergeStatus = new Set<string>();

  for (const c of cards) {
    const p = cardProject(c);
    if (p) project.add(p);
    const b = cardBatch(c);
    if (b) batch.add(b);
    const cb = cardClaimedBy(c);
    if (cb) claimedBy.add(cb);
    const t = cardPoints(c);
    if (typeof t === "number") tier.add(t);
    const s = cardStakes(c);
    if (s) stakes.add(s);
    const ms = c.frontmatter["merge_status"];
    if (typeof ms === "string") mergeStatus.add(ms);
    // depends_on is read here to keep the lint happy until we wire a
    // "blocked-only" chip. It surfaces dep info to callers that want it.
    void cardDependsOn(c);
  }

  return {
    project: [...project].sort(),
    batch: [...batch].sort(),
    claimedBy: [...claimedBy].sort(),
    tier: [...tier].sort((a, b) => a - b),
    stakes: [...stakes].sort(),
    mergeStatus: [...mergeStatus].sort(),
  };
}

/**
 * Count the currently-active chip slots (search counts as one).
 */
export function activeFilterCount(f: FilterState): number {
  let n = 0;
  if (f.search.length > 0) n++;
  if (f.project.length > 0) n++;
  if (f.batch.length > 0) n++;
  if (f.claimedBy.length > 0) n++;
  if (f.tier.length > 0) n++;
  if (f.stakes.length > 0) n++;
  if (f.pinRequired !== null) n++;
  if (f.extendedThinking !== null) n++;
  if (f.mergeStatus.length > 0) n++;
  return n;
}

/**
 * URL <-> filter codec. Sharable links carry the filter state as a
 * URLSearchParams string. Multi-selects are comma-joined; tri-state
 * booleans use literal "true"/"false". Unknown params are ignored
 * silently so an old link doesn't error after a schema change.
 */
export function filtersToParams(f: FilterState): URLSearchParams {
  const p = new URLSearchParams();
  if (f.search.length > 0) p.set("q", f.search);
  if (f.project.length > 0) p.set("project", f.project.join(","));
  if (f.batch.length > 0) p.set("batch", f.batch.join(","));
  if (f.claimedBy.length > 0) p.set("runner", f.claimedBy.join(","));
  if (f.tier.length > 0) p.set("tier", f.tier.join(","));
  if (f.stakes.length > 0) p.set("stakes", f.stakes.join(","));
  if (f.pinRequired !== null) p.set("pin", String(f.pinRequired));
  if (f.extendedThinking !== null)
    p.set("thinking", String(f.extendedThinking));
  if (f.mergeStatus.length > 0) p.set("merge", f.mergeStatus.join(","));
  return p;
}

export function filtersFromParams(p: URLSearchParams): FilterState {
  return {
    search: p.get("q") ?? "",
    project: splitCsv(p.get("project")),
    batch: splitCsv(p.get("batch")),
    claimedBy: splitCsv(p.get("runner")),
    tier: splitCsv(p.get("tier"))
      .map((s) => Number.parseInt(s, 10))
      .filter((n) => Number.isFinite(n)),
    stakes: splitCsv(p.get("stakes")),
    pinRequired: parseTri(p.get("pin")),
    extendedThinking: parseTri(p.get("thinking")),
    mergeStatus: splitCsv(p.get("merge")),
  };
}

function splitCsv(v: string | null): string[] {
  if (!v) return [];
  return v
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function parseTri(v: string | null): boolean | null {
  if (v === "true") return true;
  if (v === "false") return false;
  return null;
}
