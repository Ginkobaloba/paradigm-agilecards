/**
 * Per-card lifecycle timeline. Loads the persisted history from
 * /api/cards/:id/events on mount, then patches it forward from live
 * `card-event-added` SSE events delivered via the card-event bus.
 *
 * Adjacent heartbeats collapse into a single row so the runner's keepalive
 * doesn't drown out the events the operator actually cares about.
 */

import { useEffect, useMemo, useState } from "react";

import { api, ApiError, type CardEventRow } from "../lib/api";
import { subscribe } from "../lib/cardEventBus";
import { relativeTime } from "../lib/relativeTime";
import { groupTimeline, type TimelineItem } from "../lib/timelineGroup";

interface Props {
  cardId: string;
}

/**
 * Display label + dot color for each event type. Unknown types fall back
 * to a neutral muted dot so we never throw on a new server-emitted type.
 */
const EVENT_META: Record<
  string,
  { label: string; dotClass: string }
> = {
  discovered: { label: "Discovered", dotClass: "bg-muted" },
  status_changed: { label: "Status changed", dotClass: "bg-accent" },
  started: { label: "Started", dotClass: "bg-accent" },
  released: { label: "Released", dotClass: "bg-warn" },
  heartbeat: { label: "Heartbeat", dotClass: "bg-muted" },
  finished: { label: "Finished", dotClass: "bg-ok" },
  verifier_called: { label: "Verifier called", dotClass: "bg-accent" },
  cascade: { label: "Cascade", dotClass: "bg-warn" },
  merge_status_changed: { label: "Merge status", dotClass: "bg-ok" },
};

function metaFor(type: string): { label: string; dotClass: string } {
  return EVENT_META[type] ?? { label: type, dotClass: "bg-muted/60" };
}

export function Timeline({ cardId }: Props) {
  const [events, setEvents] = useState<CardEventRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setEvents(null);
    setError(null);
    void api
      .listCardEvents(cardId, { limit: 200 })
      .then((res) => {
        if (cancelled) return;
        setEvents(res.events);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [cardId]);

  useEffect(() => {
    return subscribe((evt) => {
      if (evt.cardId !== cardId) return;
      setEvents((cur) => {
        if (cur === null) return cur;
        if (cur.some((e) => e.id === evt.id)) return cur;
        return [...cur, evt];
      });
    });
  }, [cardId]);

  const items: TimelineItem[] = useMemo(
    () => (events ? groupTimeline(events) : []),
    [events]
  );

  return (
    <section className="surface-2 rounded">
      <button
        type="button"
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold text-text border-b border-border"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
      >
        <span>
          Timeline{" "}
          {events ? (
            <span className="text-muted font-normal">({events.length})</span>
          ) : null}
        </span>
        <span className="text-muted">{collapsed ? "show" : "hide"}</span>
      </button>
      {!collapsed && (
        <div className="px-3 py-2">
          {error ? (
            <div className="text-danger text-xs">{error}</div>
          ) : events === null ? (
            <div className="text-muted text-xs italic">loading timeline…</div>
          ) : events.length === 0 ? (
            <div className="text-muted text-xs italic">No events yet.</div>
          ) : (
            <ol className="flex flex-col gap-1.5 text-xs">
              {items.map((item, i) => (
                <TimelineRow key={i} item={item} />
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

function TimelineRow({ item }: { item: TimelineItem }) {
  if (item.kind === "heartbeat-group") {
    const first = relativeTime(Date.parse(item.firstAt));
    const last = relativeTime(Date.parse(item.lastAt));
    return (
      <li className="flex items-center gap-2 text-muted">
        <span className="w-2 h-2 rounded-full bg-muted/60 shrink-0" />
        <span>
          {item.count === 1
            ? "1 heartbeat"
            : `${item.count} heartbeats`}
          {item.by ? ` by ${item.by}` : ""}
          {first && last
            ? ` — ${first.label}${first.label !== last.label ? ` → ${last.label}` : ""}`
            : ""}
        </span>
      </li>
    );
  }
  const meta = metaFor(item.event.type);
  const rel = relativeTime(Date.parse(item.event.at));
  return (
    <li className="flex items-start gap-2">
      <span
        className={`w-2 h-2 rounded-full ${meta.dotClass} shrink-0 mt-[5px]`}
        aria-hidden
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="font-medium text-text">{meta.label}</span>
          {rel ? (
            <span className="text-muted/80" title={item.event.at}>
              {rel.label}
            </span>
          ) : null}
        </div>
        <TimelineDetails details={item.event.details} />
      </div>
    </li>
  );
}

function TimelineDetails({ details }: { details: unknown }) {
  if (details === null || details === undefined) return null;
  if (typeof details === "string" || typeof details === "number") {
    return <div className="text-muted">{String(details)}</div>;
  }
  if (typeof details !== "object") return null;
  const entries = Object.entries(details as Record<string, unknown>).filter(
    ([, v]) => v !== null && v !== undefined && v !== ""
  );
  if (entries.length === 0) return null;
  return (
    <div className="text-muted truncate">
      {entries
        .map(([k, v]) => `${k}: ${stringify(v)}`)
        .join(" · ")}
    </div>
  );
}

function stringify(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
