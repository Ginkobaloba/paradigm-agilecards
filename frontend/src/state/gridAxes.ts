/**
 * Grid axis selection, lifted out of the Grid route's local state so a
 * saved view can capture and restore it. The grid plots cards on an
 * (x, y) plane; which card field each axis maps to is a view-level
 * concern, the same way filters and the group-by lens are.
 *
 * Kept deliberately tiny and store-shaped (mirrors useFilters / useLens)
 * so ViewMenu reads and writes it without the Grid component being
 * mounted -- you can save "the grid axes I like" from any route.
 */

import { create } from "zustand";

import type { AxisKey } from "../lib/gridLayout";

export const DEFAULT_X_AXIS: AxisKey = "cost";
export const DEFAULT_Y_AXIS: AxisKey = "stakes";

interface GridAxesState {
  xAxis: AxisKey;
  yAxis: AxisKey;
  setXAxis: (a: AxisKey) => void;
  setYAxis: (a: AxisKey) => void;
  /** Apply both at once -- used when loading a saved view. */
  setAxes: (x: AxisKey, y: AxisKey) => void;
}

export const useGridAxes = create<GridAxesState>((set) => ({
  xAxis: DEFAULT_X_AXIS,
  yAxis: DEFAULT_Y_AXIS,
  setXAxis: (xAxis) => set({ xAxis }),
  setYAxis: (yAxis) => set({ yAxis }),
  setAxes: (xAxis, yAxis) => set({ xAxis, yAxis }),
}));
