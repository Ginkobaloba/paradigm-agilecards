/**
 * Soft WIP (work-in-progress) limits per column. When the visible card
 * count in a column exceeds its limit, the column header surfaces a
 * warn-colored pill so the operator knows there's a queue building up.
 *
 * Defaults today are hardcoded -- the roadmap (item 2.7) ties these to
 * "number of configured parallel runners," but that signal doesn't
 * exist in the system yet. When runner-config visibility lands, the
 * default constructor swaps from `DEFAULT_LIMITS[status]` to whatever
 * the runner reports, and the override path stays unchanged.
 *
 * Overrides live in localStorage; the user edits per-column via a tiny
 * popover on the column header. Setting a limit to `null` means
 * "unlimited" (no warning regardless of count).
 */

import { create } from "zustand";

import type { StatusId } from "../lib/api";

const STORAGE_KEY = "agile-cards-board.wipLimits";

/**
 * Out-of-the-box caps. Backlog / Done / Blocked are intentionally
 * unlimited -- WIP is the constraint we care about, and the agent's
 * concurrency cap binds on Active first.
 */
export const DEFAULT_LIMITS: Readonly<Record<StatusId, number | null>> = {
  backlog: null,
  active: 3,
  awaiting_amendment_review: 5,
  done: null,
  blocked: null,
};

type LimitMap = Record<StatusId, number | null>;

function loadPersisted(): Partial<LimitMap> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<LimitMap>;
    return parsed ?? {};
  } catch {
    return {};
  }
}

function persist(state: Partial<LimitMap>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // accept the loss
  }
}

interface State {
  /** Overrides only -- missing keys fall back to DEFAULT_LIMITS. */
  overrides: Partial<LimitMap>;
  setLimit: (status: StatusId, limit: number | null) => void;
  clearOverride: (status: StatusId) => void;
}

export const useWipLimits = create<State>((set) => {
  const initial = loadPersisted();
  return {
    overrides: initial,
    setLimit: (status, limit) =>
      set((s) => {
        const next = { ...s.overrides, [status]: limit };
        persist(next);
        return { overrides: next };
      }),
    clearOverride: (status) =>
      set((s) => {
        const next = { ...s.overrides };
        delete next[status];
        persist(next);
        return { overrides: next };
      }),
  };
});

export function effectiveLimit(
  status: StatusId,
  overrides: Partial<LimitMap>
): number | null {
  if (status in overrides) {
    const ov = overrides[status];
    return ov === undefined ? DEFAULT_LIMITS[status] : ov;
  }
  return DEFAULT_LIMITS[status];
}

/**
 * Compute the limit state for a column given the current cards visible
 * in it. Returns:
 *  - `null` when the column has no limit (unlimited)
 *  - `{ limit, count, over: boolean }` otherwise
 */
export interface LimitState {
  readonly limit: number;
  readonly count: number;
  readonly over: boolean;
  readonly atCap: boolean;
}

export function limitStateFor(
  status: StatusId,
  cardCount: number,
  overrides: Partial<LimitMap>
): LimitState | null {
  const limit = effectiveLimit(status, overrides);
  if (limit === null) return null;
  return {
    limit,
    count: cardCount,
    over: cardCount > limit,
    atCap: cardCount === limit,
  };
}
