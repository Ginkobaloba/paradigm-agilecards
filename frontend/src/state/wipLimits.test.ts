import { describe, it, expect } from "vitest";

import {
  DEFAULT_LIMITS,
  effectiveLimit,
  limitStateFor,
} from "./wipLimits";

describe("effectiveLimit", () => {
  it("returns the default when no override is set", () => {
    expect(effectiveLimit("active", {})).toBe(DEFAULT_LIMITS.active);
    expect(effectiveLimit("backlog", {})).toBe(null);
  });

  it("returns the override when set", () => {
    expect(effectiveLimit("active", { active: 7 })).toBe(7);
    expect(effectiveLimit("backlog", { backlog: 10 })).toBe(10);
  });

  it("treats `null` override as unlimited (overriding the default)", () => {
    expect(effectiveLimit("active", { active: null })).toBe(null);
  });
});

describe("limitStateFor", () => {
  it("returns null when the column is unlimited", () => {
    expect(limitStateFor("backlog", 100, {})).toBe(null);
  });

  it("flags `over` when count exceeds limit", () => {
    const s = limitStateFor("active", 5, {});
    expect(s).not.toBeNull();
    expect(s?.over).toBe(true);
    expect(s?.atCap).toBe(false);
    expect(s?.limit).toBe(DEFAULT_LIMITS.active);
    expect(s?.count).toBe(5);
  });

  it("flags `atCap` when count equals limit and not over", () => {
    const s = limitStateFor("active", DEFAULT_LIMITS.active!, {});
    expect(s?.atCap).toBe(true);
    expect(s?.over).toBe(false);
  });

  it("under cap is neither over nor atCap", () => {
    const s = limitStateFor("active", 1, {});
    expect(s?.over).toBe(false);
    expect(s?.atCap).toBe(false);
  });

  it("override applies to the limit-state calculation", () => {
    const s = limitStateFor("active", 4, { active: 2 });
    expect(s?.limit).toBe(2);
    expect(s?.over).toBe(true);
  });
});
