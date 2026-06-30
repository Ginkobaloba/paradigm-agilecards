/**
 * Pure event derivation. Given two snapshots of a card (previous + current)
 * compute the lifecycle events that happened between them. The watcher
 * persists what this returns; the SSE bus republishes them so the frontend
 * timeline can render live.
 *
 * Why derive instead of having the runner publish events directly: today
 * the runner only edits the card frontmatter on disk. The chokidar watcher
 * is what observes that. Reverse-engineering the events from the diff
 * keeps the runner contract simple (write the field, we'll notice). When
 * the runner gains a structured event stream later, this module becomes a
 * thin adapter rather than a reverse-engineer.
 */

export type CardEventType =
  | "discovered"
  | "status_changed"
  | "started"
  | "released"
  | "heartbeat"
  | "finished"
  | "verifier_called"
  | "cascade"
  | "merge_status_changed";

export interface CardSnapshot {
  readonly id: string;
  readonly status: string;
  readonly frontmatter: Record<string, unknown>;
  readonly mtimeMs: number;
}

export interface DerivedEvent {
  readonly cardId: string;
  readonly type: CardEventType;
  /** ISO 8601 timestamp. */
  readonly at: string;
  readonly details?: unknown;
}

function isString(v: unknown): v is string {
  return typeof v === "string" && v.length > 0;
}

function isNonEmpty(v: unknown): boolean {
  return v !== null && v !== undefined && !(typeof v === "string" && v === "");
}

/**
 * Coerce a frontmatter timestamp value to a canonical ISO string. If it's
 * not a parseable date, fall back to the snapshot's mtime.
 */
function toIso(value: unknown, fallbackMs: number): string {
  if (typeof value === "string") {
    const t = Date.parse(value);
    if (!Number.isNaN(t)) return new Date(t).toISOString();
  }
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return value.toISOString();
  }
  return new Date(fallbackMs).toISOString();
}

function modelOf(fm: Record<string, unknown>): string | null {
  const used = fm["model_used"];
  if (isString(used)) return used;
  const planned = fm["model"];
  if (isString(planned)) return planned;
  return null;
}

export function deriveEvents(
  prev: CardSnapshot | null,
  current: CardSnapshot
): DerivedEvent[] {
  const events: DerivedEvent[] = [];
  const fm = current.frontmatter;
  const fallback = current.mtimeMs;

  if (prev === null) {
    events.push({
      cardId: current.id,
      type: "discovered",
      at: new Date(fallback).toISOString(),
      details: { status: current.status },
    });
    return events;
  }

  const prevFm = prev.frontmatter;

  if (prev.status !== current.status) {
    events.push({
      cardId: current.id,
      type: "status_changed",
      at: new Date(fallback).toISOString(),
      details: { from: prev.status, to: current.status },
    });
  }

  const prevClaimed = prevFm["claimed_by"];
  const currClaimed = fm["claimed_by"];
  if (!isNonEmpty(prevClaimed) && isNonEmpty(currClaimed)) {
    events.push({
      cardId: current.id,
      type: "started",
      at: toIso(fm["started_at"], fallback),
      details: { by: currClaimed, model: modelOf(fm) },
    });
  } else if (isNonEmpty(prevClaimed) && !isNonEmpty(currClaimed)) {
    events.push({
      cardId: current.id,
      type: "released",
      at: new Date(fallback).toISOString(),
      details: { from: prevClaimed },
    });
  }

  const prevHb = prevFm["last_heartbeat"];
  const currHb = fm["last_heartbeat"];
  if (isNonEmpty(currHb) && currHb !== prevHb) {
    events.push({
      cardId: current.id,
      type: "heartbeat",
      at: toIso(currHb, fallback),
      details: { by: currClaimed ?? null },
    });
  }

  const prevFinished = prevFm["finished_at"];
  const currFinished = fm["finished_at"];
  if (!isNonEmpty(prevFinished) && isNonEmpty(currFinished)) {
    const tokens = fm["actual_tokens"];
    events.push({
      cardId: current.id,
      type: "finished",
      at: toIso(currFinished, fallback),
      details: {
        tokens: typeof tokens === "number" ? tokens : null,
        model: modelOf(fm),
      },
    });
  }

  const prevVerified = prevFm["verified_at"];
  const currVerified = fm["verified_at"];
  if (!isNonEmpty(prevVerified) && isNonEmpty(currVerified)) {
    events.push({
      cardId: current.id,
      type: "verifier_called",
      at: toIso(currVerified, fallback),
      details: { by: fm["verified_by"] ?? null },
    });
  }

  // cascade_history is an append-only array. Emit one event per new entry.
  const prevCascade = Array.isArray(prevFm["cascade_history"])
    ? (prevFm["cascade_history"] as unknown[])
    : [];
  const currCascade = Array.isArray(fm["cascade_history"])
    ? (fm["cascade_history"] as unknown[])
    : [];
  if (currCascade.length > prevCascade.length) {
    for (let i = prevCascade.length; i < currCascade.length; i++) {
      const entry = currCascade[i];
      const at =
        entry && typeof entry === "object" && "at" in entry
          ? toIso((entry as { at: unknown }).at, fallback)
          : new Date(fallback).toISOString();
      events.push({
        cardId: current.id,
        type: "cascade",
        at,
        details: entry,
      });
    }
  }

  const prevMerge = prevFm["merge_status"];
  const currMerge = fm["merge_status"];
  if (isNonEmpty(currMerge) && currMerge !== prevMerge) {
    events.push({
      cardId: current.id,
      type: "merge_status_changed",
      at: new Date(fallback).toISOString(),
      details: { from: prevMerge ?? null, to: currMerge },
    });
  }

  return events;
}
