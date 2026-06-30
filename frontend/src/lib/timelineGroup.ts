/**
 * Visual grouping for the card timeline. Adjacent heartbeats collapse to
 * a single "N heartbeats over Xm" row so a noisy runner doesn't drown
 * out the events the operator cares about. Everything else passes
 * through as a `single` row.
 *
 * Pure function, unit-tested separately.
 */

import type { CardEventRow } from "./api";

export type TimelineItem =
  | { kind: "single"; event: CardEventRow }
  | {
      kind: "heartbeat-group";
      count: number;
      firstAt: string;
      lastAt: string;
      by: string | null;
      ids: number[];
    };

function heartbeatBy(e: CardEventRow): string | null {
  if (
    e.details &&
    typeof e.details === "object" &&
    "by" in e.details &&
    typeof (e.details as { by: unknown }).by === "string"
  ) {
    return (e.details as { by: string }).by;
  }
  return null;
}

export function groupTimeline(events: readonly CardEventRow[]): TimelineItem[] {
  const out: TimelineItem[] = [];
  for (const e of events) {
    if (e.type === "heartbeat") {
      const last = out[out.length - 1];
      if (last && last.kind === "heartbeat-group") {
        last.count += 1;
        last.lastAt = e.at;
        last.ids.push(e.id);
        continue;
      }
      out.push({
        kind: "heartbeat-group",
        count: 1,
        firstAt: e.at,
        lastAt: e.at,
        by: heartbeatBy(e),
        ids: [e.id],
      });
      continue;
    }
    out.push({ kind: "single", event: e });
  }
  return out;
}
