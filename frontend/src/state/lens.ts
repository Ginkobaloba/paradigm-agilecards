/**
 * "Lens" state -- how the kanban *reshapes* what filters have narrowed.
 * Today only `groupBy: 'project'` is supported; future lenses (group by
 * batch, group by runner) will land here too.
 *
 * Local-only state: persisted to localStorage so a tab reopen restores
 * the view, but not server-stored. Saved views (PR #14) already cover
 * the cross-device case; the lens lives at the same surface and could
 * be folded into a saved view later if it earns it.
 */

import { create } from "zustand";

import type { CardSummary } from "../lib/api";
import { cardProject } from "../lib/parseCard";

export type GroupBy = "none" | "project";

const STORAGE_KEY = "agile-cards-board.lens";

interface PersistShape {
  groupBy: GroupBy;
}

function loadPersisted(): PersistShape {
  if (typeof window === "undefined") return { groupBy: "none" };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { groupBy: "none" };
    const parsed = JSON.parse(raw) as Partial<PersistShape>;
    if (parsed.groupBy === "project" || parsed.groupBy === "none") {
      return { groupBy: parsed.groupBy };
    }
  } catch {
    // Ignore JSON / storage failures and fall back to defaults.
  }
  return { groupBy: "none" };
}

function persist(state: PersistShape): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage quota or private mode -- accept the loss.
  }
}

interface State {
  groupBy: GroupBy;
  setGroupBy: (v: GroupBy) => void;
}

export const useLens = create<State>((set) => {
  const initial = loadPersisted();
  return {
    groupBy: initial.groupBy,
    setGroupBy: (v) =>
      set(() => {
        persist({ groupBy: v });
        return { groupBy: v };
      }),
  };
});

/**
 * Stable label for a card's project. Returns "Unassigned" for cards
 * with no `project` frontmatter field so the partition always lands
 * the card somewhere.
 */
export const UNASSIGNED_PROJECT = "Unassigned";

export function projectKeyOf(card: CardSummary): string {
  const p = cardProject(card);
  if (typeof p === "string" && p.length > 0) return p;
  return UNASSIGNED_PROJECT;
}

export interface CardGroup {
  readonly key: string;
  readonly label: string;
  readonly cards: readonly CardSummary[];
}

/**
 * Partition cards into project groups, preserving the input ordering
 * within each group. The "Unassigned" bucket sorts last; otherwise the
 * groups appear in the order their first card appears in the input.
 *
 * Pure function; unit-tested in lens.test.ts.
 */
export function partitionByProject(
  cards: readonly CardSummary[]
): CardGroup[] {
  const order: string[] = [];
  const buckets = new Map<string, CardSummary[]>();
  for (const c of cards) {
    const key = projectKeyOf(c);
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(c);
  }
  // Move "Unassigned" to the end if it's present.
  const idx = order.indexOf(UNASSIGNED_PROJECT);
  if (idx >= 0 && idx !== order.length - 1) {
    order.splice(idx, 1);
    order.push(UNASSIGNED_PROJECT);
  }
  return order.map((key) => ({
    key,
    label: key,
    cards: buckets.get(key)!,
  }));
}
