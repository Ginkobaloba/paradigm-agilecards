import { describe, it, expect } from "vitest";

import { EMPTY_FILTERS } from "./filters";
import { DEFAULT_X_AXIS, DEFAULT_Y_AXIS } from "./gridAxes";
import {
  VIEWS_SCHEMA_VERSION,
  bundleFromCurrent,
  normalizeViewPayload,
} from "./savedView";

describe("normalizeViewPayload", () => {
  it("passes a well-formed versioned bundle through, validated", () => {
    const payload = {
      views_schema_version: 1,
      filters: { ...EMPTY_FILTERS, project: ["acme"], stakes: ["high"] },
      groupBy: "project",
      grid: { xAxis: "points", yAxis: "tier" },
    };
    const out = normalizeViewPayload(payload);
    expect(out.views_schema_version).toBe(VIEWS_SCHEMA_VERSION);
    expect(out.filters.project).toEqual(["acme"]);
    expect(out.filters.stakes).toEqual(["high"]);
    expect(out.groupBy).toBe("project");
    expect(out.grid).toEqual({ xAxis: "points", yAxis: "tier" });
  });

  it("reads a legacy bare-FilterState payload as filters-only", () => {
    // Pre-versioning rows stored the FilterState directly, with no
    // version field and no groupBy / grid.
    const legacy = { ...EMPTY_FILTERS, batch: ["b042"] };
    const out = normalizeViewPayload(legacy);
    expect(out.views_schema_version).toBe(VIEWS_SCHEMA_VERSION);
    expect(out.filters.batch).toEqual(["b042"]);
    expect(out.groupBy).toBe("none");
    expect(out.grid).toEqual({ xAxis: DEFAULT_X_AXIS, yAxis: DEFAULT_Y_AXIS });
  });

  it("falls back to all defaults for null / non-object payloads", () => {
    for (const bad of [null, undefined, 42, "nope"] as const) {
      const out = normalizeViewPayload(bad);
      expect(out.filters).toEqual(EMPTY_FILTERS);
      expect(out.groupBy).toBe("none");
      expect(out.grid).toEqual({ xAxis: DEFAULT_X_AXIS, yAxis: DEFAULT_Y_AXIS });
    }
  });

  it("defaults the grid when axes are invalid or identical", () => {
    const bogusAxis = normalizeViewPayload({
      views_schema_version: 1,
      grid: { xAxis: "nonsense", yAxis: "stakes" },
    });
    expect(bogusAxis.grid).toEqual({
      xAxis: DEFAULT_X_AXIS,
      yAxis: DEFAULT_Y_AXIS,
    });
    // Two fields can't share an axis; fall back rather than render an
    // empty plot.
    const collided = normalizeViewPayload({
      views_schema_version: 1,
      grid: { xAxis: "cost", yAxis: "cost" },
    });
    expect(collided.grid).toEqual({
      xAxis: DEFAULT_X_AXIS,
      yAxis: DEFAULT_Y_AXIS,
    });
  });

  it("defaults groupBy when it is not a known lens value", () => {
    const out = normalizeViewPayload({
      views_schema_version: 1,
      groupBy: "by-the-phase-of-the-moon",
    });
    expect(out.groupBy).toBe("none");
  });

  it("ignores unknown filter keys while keeping known ones", () => {
    const out = normalizeViewPayload({
      views_schema_version: 1,
      filters: { project: ["x"], bogusKey: "ignored" },
    });
    expect(out.filters.project).toEqual(["x"]);
    // Baseline fields are still present from EMPTY_FILTERS.
    expect(out.filters.search).toBe("");
    expect(out.filters.tier).toEqual([]);
  });
});

describe("bundleFromCurrent", () => {
  it("stamps the current schema version and carries the state through", () => {
    const bundle = bundleFromCurrent({
      filters: { ...EMPTY_FILTERS, stakes: ["low"] },
      groupBy: "project",
      grid: { xAxis: "cost", yAxis: "points" },
    });
    expect(bundle.views_schema_version).toBe(VIEWS_SCHEMA_VERSION);
    expect(bundle.filters.stakes).toEqual(["low"]);
    expect(bundle.groupBy).toBe("project");
    expect(bundle.grid).toEqual({ xAxis: "cost", yAxis: "points" });
  });

  it("round-trips through normalizeViewPayload unchanged", () => {
    const bundle = bundleFromCurrent({
      filters: { ...EMPTY_FILTERS, project: ["p1"] },
      groupBy: "project",
      grid: { xAxis: "tier", yAxis: "stakes" },
    });
    expect(normalizeViewPayload(bundle)).toEqual(bundle);
  });
});
