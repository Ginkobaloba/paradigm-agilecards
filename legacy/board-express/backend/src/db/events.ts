/**
 * Per-card event log. Persists what `events/derive.ts` produces so the
 * card detail modal can render a lifecycle timeline.
 *
 * Storage shape: one row per derived event, JSON-stringified details. The
 * details column is opaque to SQL; the frontend renders it. That keeps the
 * schema fixed even as new event types ship.
 */

import type { Db } from "./sqlite.js";
import { getDb } from "./sqlite.js";

export interface CardEventRow {
  id: number;
  cardId: string;
  type: string;
  at: string;
  details: unknown;
}

export interface AppendInput {
  cardId: string;
  type: string;
  at: string;
  details?: unknown;
}

interface RawRow {
  id: number;
  card_id: string;
  type: string;
  at: string;
  details: string | null;
}

function hydrate(row: RawRow): CardEventRow {
  let details: unknown = null;
  if (row.details !== null) {
    try {
      details = JSON.parse(row.details);
    } catch {
      details = row.details;
    }
  }
  return {
    id: row.id,
    cardId: row.card_id,
    type: row.type,
    at: row.at,
    details,
  };
}

export function appendEvent(
  input: AppendInput,
  db: Db = getDb()
): CardEventRow {
  const json = input.details === undefined ? null : JSON.stringify(input.details);
  const info = db
    .prepare(
      `INSERT INTO card_events (card_id, type, at, details)
       VALUES (?, ?, ?, ?)`
    )
    .run(input.cardId, input.type, input.at, json);
  const id = Number(info.lastInsertRowid);
  return {
    id,
    cardId: input.cardId,
    type: input.type,
    at: input.at,
    details: input.details ?? null,
  };
}

export function getEventsForCard(
  cardId: string,
  opts: { limit?: number; since?: string } = {},
  db: Db = getDb()
): CardEventRow[] {
  const limit = Math.max(1, Math.min(opts.limit ?? 500, 1000));
  const since = opts.since ?? null;

  const rows = since
    ? db
        .prepare<
          [string, string, number],
          RawRow
        >(
          `SELECT id, card_id, type, at, details
             FROM card_events
            WHERE card_id = ? AND at > ?
         ORDER BY id ASC
            LIMIT ?`
        )
        .all(cardId, since, limit)
    : db
        .prepare<[string, number], RawRow>(
          `SELECT id, card_id, type, at, details
             FROM card_events
            WHERE card_id = ?
         ORDER BY id ASC
            LIMIT ?`
        )
        .all(cardId, limit);

  return rows.map(hydrate);
}

/** Count events for a card. Used by tests and for backfill heuristics. */
export function countEventsForCard(
  cardId: string,
  db: Db = getDb()
): number {
  const row = db
    .prepare<[string], { n: number }>(
      `SELECT COUNT(*) AS n FROM card_events WHERE card_id = ?`
    )
    .get(cardId);
  return row?.n ?? 0;
}
