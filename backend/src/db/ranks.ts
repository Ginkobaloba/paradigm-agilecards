/**
 * Manual-rank storage. The board defaults to sorting each column by this
 * rank; drag-to-reorder writes a new midpoint here.
 *
 * Why floats: classic midpoint ranking. Inserting between two cards is
 * `(prev + next) / 2`; over time the gap halves until it loses precision
 * (~2^53 inserts at the same midpoint, which we will not hit). A
 * lex-string rank (LexoRank) would be more robust at extreme density,
 * but a small board does not need it and a REAL column keeps the SQL
 * trivial.
 *
 * On cross-column move we drop the old rank and assign a fresh one at
 * the end of the new column. That keeps `(status, rank)` unique per
 * column and matches how a user expects "I just moved this here" to
 * behave (lands at the bottom of the new column).
 */

import type { Db } from "./sqlite.js";
import { getDb } from "./sqlite.js";

/** Default starting rank for the first card in any column. */
export const RANK_BASE = 1024;
/** Default step appended past the last existing rank. */
export const RANK_STEP = 1024;

export interface RankRow {
  cardId: string;
  status: string;
  rank: number;
}

/**
 * Return every persisted rank as a card-id -> row map. The frontend
 * loads this once at boot and patches it in response to SSE events.
 */
export function getAllRanks(db: Db = getDb()): RankRow[] {
  const rows = db
    .prepare<unknown[], { card_id: string; status: string; rank: number }>(
      `SELECT card_id, status, rank FROM card_rank`
    )
    .all();
  return rows.map((r) => ({ cardId: r.card_id, status: r.status, rank: r.rank }));
}

/** Look up the rank for a single card. */
export function getRank(
  cardId: string,
  db: Db = getDb()
): RankRow | null {
  const row = db
    .prepare<
      [string],
      { card_id: string; status: string; rank: number } | undefined
    >(`SELECT card_id, status, rank FROM card_rank WHERE card_id = ?`)
    .get(cardId);
  if (!row) return null;
  return { cardId: row.card_id, status: row.status, rank: row.rank };
}

/**
 * Return the largest rank currently in a status. Used to append a card
 * to the end of a column on cross-column move.
 */
export function maxRankInStatus(
  status: string,
  db: Db = getDb()
): number | null {
  const row = db
    .prepare<[string], { max_rank: number | null }>(
      `SELECT MAX(rank) AS max_rank FROM card_rank WHERE status = ?`
    )
    .get(status);
  if (!row || row.max_rank === null) return null;
  return row.max_rank;
}

/**
 * Compute and persist a new rank that places `cardId` between `prev` and
 * `next` in `status`. Missing neighbors fall back to base/step rules:
 *   - prev only           -> prev + RANK_STEP
 *   - next only           -> next - RANK_STEP
 *   - neither             -> max(existing in status) + RANK_STEP, or RANK_BASE
 *   - both                -> (prev + next) / 2
 *
 * `prevId` / `nextId` are card ids; we look up their persisted ranks
 * server-side so two concurrent clients can't disagree on what counts as
 * "between".
 *
 * Returns the new rank value.
 */
export function setRankBetween(
  cardId: string,
  status: string,
  prevId: string | null,
  nextId: string | null,
  db: Db = getDb()
): number {
  const prev = prevId ? getRank(prevId, db) : null;
  const next = nextId ? getRank(nextId, db) : null;

  let rank: number;
  if (prev !== null && next !== null) {
    rank = (prev.rank + next.rank) / 2;
  } else if (prev !== null) {
    rank = prev.rank + RANK_STEP;
  } else if (next !== null) {
    rank = next.rank - RANK_STEP;
  } else {
    const existing = maxRankInStatus(status, db);
    rank = existing === null ? RANK_BASE : existing + RANK_STEP;
  }

  upsertRank(cardId, status, rank, db);
  return rank;
}

/**
 * Append a card to the end of a column's ranking. Used by the
 * cross-column move path so a freshly-arrived card lands at the bottom
 * of the new column.
 */
export function appendRank(
  cardId: string,
  status: string,
  db: Db = getDb()
): number {
  const existing = maxRankInStatus(status, db);
  const rank = existing === null ? RANK_BASE : existing + RANK_STEP;
  upsertRank(cardId, status, rank, db);
  return rank;
}

/** Direct upsert, used when we already know the rank value. */
export function upsertRank(
  cardId: string,
  status: string,
  rank: number,
  db: Db = getDb()
): void {
  db.prepare(
    `INSERT INTO card_rank (card_id, status, rank, updated_at)
     VALUES (?, ?, ?, datetime('now'))
     ON CONFLICT(card_id) DO UPDATE SET
       status = excluded.status,
       rank = excluded.rank,
       updated_at = excluded.updated_at`
  ).run(cardId, status, rank);
}

export function removeRank(cardId: string, db: Db = getDb()): void {
  db.prepare(`DELETE FROM card_rank WHERE card_id = ?`).run(cardId);
}
