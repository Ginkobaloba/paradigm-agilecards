/**
 * The shape a saved view persists, plus the normalizer that reads it.
 *
 * History: views originally stored a bare `FilterState` as their opaque
 * payload. v0 of "complete saved views" promotes that to a versioned
 * bundle that also carries the group-by lens and the grid axes, so a
 * saved view restores everything the operator can see, not just the
 * filter chips.
 *
 * The backend stores the payload opaquely (a 16 KB JSON blob keyed by
 * token + name), so there is no server migration: old rows simply lack
 * the `views_schema_version` field, and `normalizeViewPayload` resolves
 * them as filters-only. New saves always write the versioned bundle.
 */

import { EMPTY_FILTERS, type FilterState } from "./filters";
import type { GroupBy } from "./lens";
import { DEFAULT_X_AXIS, DEFAULT_Y_AXIS } from "./gridAxes";
import { AXIS_OPTIONS, type AxisKey } from "../lib/gridLayout";

export const VIEWS_SCHEMA_VERSION = 1;

export interface GridAxesPayload {
  xAxis: AxisKey;
  yAxis: AxisKey;
}

export interface ViewBundle {
  views_schema_version: number;
  filters: FilterState;
  groupBy: GroupBy;
  grid: GridAxesPayload;
}

function isAxisKey(v: unknown): v is AxisKey {
  return typeof v === "string" && AXIS_OPTIONS.some((o) => o.key === v);
}

function isGroupBy(v: unknown): v is GroupBy {
  return v === "none" || v === "project";
}

/**
 * Merge an unknown payload fragment over the empty-filter baseline.
 * Unknown / malformed input collapses to "no filters" rather than
 * throwing, so a corrupt saved row never wedges the menu.
 */
function normalizeFilters(v: unknown): FilterState {
  if (v === null || typeof v !== "object") return { ...EMPTY_FILTERS };
  return { ...EMPTY_FILTERS, ...(v as Partial<FilterState>) };
}

/**
 * Grid axes must both be valid axis keys AND distinct (the plot can't
 * map two fields to the same axis). Anything else falls back to the
 * defaults so a hand-edited or stale payload can't render an empty plot.
 */
function normalizeGrid(v: unknown): GridAxesPayload {
  if (v !== null && typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (isAxisKey(o["xAxis"]) && isAxisKey(o["yAxis"]) && o["xAxis"] !== o["yAxis"]) {
      return { xAxis: o["xAxis"], yAxis: o["yAxis"] };
    }
  }
  return { xAxis: DEFAULT_X_AXIS, yAxis: DEFAULT_Y_AXIS };
}

/**
 * Read any historical or current payload into a complete ViewBundle.
 *
 *   - Versioned payload (`views_schema_version` present): validate each
 *     field, falling back to defaults on anything malformed.
 *   - Legacy payload (a bare FilterState, no version field): treat the
 *     whole payload as the filter state; group-by and grid take defaults.
 *   - null / garbage: everything defaults.
 */
export function normalizeViewPayload(payload: unknown): ViewBundle {
  if (
    payload !== null &&
    typeof payload === "object" &&
    "views_schema_version" in (payload as Record<string, unknown>)
  ) {
    const p = payload as Record<string, unknown>;
    return {
      views_schema_version: VIEWS_SCHEMA_VERSION,
      filters: normalizeFilters(p["filters"]),
      groupBy: isGroupBy(p["groupBy"]) ? p["groupBy"] : "none",
      grid: normalizeGrid(p["grid"]),
    };
  }
  // Legacy bare-FilterState row (or empty/garbage): filters-only.
  return {
    views_schema_version: VIEWS_SCHEMA_VERSION,
    filters: normalizeFilters(payload),
    groupBy: "none",
    grid: { xAxis: DEFAULT_X_AXIS, yAxis: DEFAULT_Y_AXIS },
  };
}

/**
 * Build the bundle to persist from the current store state. Always
 * stamps the current schema version.
 */
export function bundleFromCurrent(args: {
  filters: FilterState;
  groupBy: GroupBy;
  grid: GridAxesPayload;
}): ViewBundle {
  return {
    views_schema_version: VIEWS_SCHEMA_VERSION,
    filters: args.filters,
    groupBy: args.groupBy,
    grid: args.grid,
  };
}
