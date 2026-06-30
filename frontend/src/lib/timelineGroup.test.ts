import { describe, it, expect } from "vitest";

import type { CardEventRow } from "./api";
import { groupTimeline } from "./timelineGroup";

function ev(over: Partial<CardEventRow>): CardEventRow {
  return {
    id: 1,
    cardId: "c",
    type: "heartbeat",
    at: "2026-05-22T10:00:00.000Z",
    details: null,
    ...over,
  };
}

describe("groupTimeline", () => {
  it("passes singletons through unchanged", () => {
    const out = groupTimeline([ev({ id: 1, type: "started" })]);
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({
      kind: "single",
      event: ev({ id: 1, type: "started" }),
    });
  });

  it("collapses adjacent heartbeats into one group", () => {
    const events = [
      ev({ id: 1, type: "started", at: "2026-05-22T10:00:00.000Z" }),
      ev({ id: 2, type: "heartbeat", at: "2026-05-22T10:01:00.000Z", details: { by: "r1" } }),
      ev({ id: 3, type: "heartbeat", at: "2026-05-22T10:02:00.000Z", details: { by: "r1" } }),
      ev({ id: 4, type: "heartbeat", at: "2026-05-22T10:03:00.000Z", details: { by: "r1" } }),
      ev({ id: 5, type: "finished", at: "2026-05-22T10:05:00.000Z" }),
    ];
    const out = groupTimeline(events);
    expect(out).toHaveLength(3);
    expect(out[0]?.kind).toBe("single");
    expect(out[1]).toEqual({
      kind: "heartbeat-group",
      count: 3,
      firstAt: "2026-05-22T10:01:00.000Z",
      lastAt: "2026-05-22T10:03:00.000Z",
      by: "r1",
      ids: [2, 3, 4],
    });
    expect(out[2]?.kind).toBe("single");
  });

  it("does not collapse heartbeats interrupted by another event", () => {
    const events = [
      ev({ id: 1, type: "heartbeat", at: "10:00" }),
      ev({ id: 2, type: "cascade", at: "10:01" }),
      ev({ id: 3, type: "heartbeat", at: "10:02" }),
    ];
    const out = groupTimeline(events);
    expect(out).toHaveLength(3);
    expect(out[0]?.kind).toBe("heartbeat-group");
    expect(out[1]?.kind).toBe("single");
    expect(out[2]?.kind).toBe("heartbeat-group");
  });

  it("returns empty array on empty input", () => {
    expect(groupTimeline([])).toEqual([]);
  });
});
