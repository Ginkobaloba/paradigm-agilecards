import { describe, expect, it } from "vitest";

import { relativeTime } from "./relativeTime";

const NOW = new Date("2026-05-20T10:00:00Z").getTime();

describe("relativeTime", () => {
  it("returns null when mtimeMs is missing or invalid", () => {
    expect(relativeTime(null)).toBeNull();
    expect(relativeTime(undefined)).toBeNull();
    expect(relativeTime(NaN as unknown as number)).toBeNull();
  });

  it("returns null when the timestamp is in the future (clock skew)", () => {
    expect(relativeTime(NOW + 60_000, { now: NOW })).toBeNull();
  });

  it("formats seconds compactly", () => {
    const r = relativeTime(NOW - 5_000, { now: NOW });
    expect(r?.label).toBe("5s");
  });

  it("formats minutes compactly", () => {
    const r = relativeTime(NOW - 3 * 60_000, { now: NOW });
    expect(r?.label).toBe("3m");
  });

  it("formats hours compactly", () => {
    const r = relativeTime(NOW - 2 * 60 * 60_000, { now: NOW });
    expect(r?.label).toBe("2h");
  });

  it("formats days compactly", () => {
    const r = relativeTime(NOW - 4 * 24 * 60 * 60_000, { now: NOW });
    expect(r?.label).toBe("4d");
  });

  it("never sets stale when isStaleEligible is false", () => {
    const tenDaysAgo = NOW - 10 * 24 * 60 * 60_000;
    expect(relativeTime(tenDaysAgo, { now: NOW })?.stale).toBe(false);
  });

  it("does not set stale within the 3-day threshold", () => {
    const r = relativeTime(NOW - 2 * 24 * 60 * 60_000, {
      now: NOW,
      isStaleEligible: true,
    });
    expect(r?.stale).toBe(false);
  });

  it("sets stale after the 3-day threshold when eligible", () => {
    const r = relativeTime(NOW - 4 * 24 * 60 * 60_000, {
      now: NOW,
      isStaleEligible: true,
    });
    expect(r?.stale).toBe(true);
  });
});
