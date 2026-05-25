/**
 * Saved-view persistence. A view is the user's "preferred way of
 * looking at the board today" -- it bundles filters, sort, and
 * grouping. We persist the whole thing as JSON in `payload`; the
 * backend treats it as opaque, the frontend owns the schema.
 *
 * Views are keyed by token_id (the bearer token is our user proxy
 * until multi-user/account work lands). Sharing a view across users
 * happens through URL encoding, not through this table.
 */

import type { Db } from "./sqlite.js";
import { getDb } from "./sqlite.js";

export interface SavedView {
  id: number;
  tokenId: number;
  name: string;
  payload: unknown;
  createdAt: string;
  updatedAt: string;
}

interface ViewRow {
  id: number;
  token_id: number;
  name: string;
  payload: string;
  created_at: string;
  updated_at: string;
}

function fromRow(row: ViewRow): SavedView {
  let parsed: unknown;
  try {
    parsed = JSON.parse(row.payload);
  } catch {
    parsed = null;
  }
  return {
    id: row.id,
    tokenId: row.token_id,
    name: row.name,
    payload: parsed,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export function listViews(
  tokenId: number,
  db: Db = getDb()
): SavedView[] {
  const rows = db
    .prepare<[number], ViewRow>(
      `SELECT id, token_id, name, payload, created_at, updated_at
         FROM saved_views
        WHERE token_id = ?
        ORDER BY name ASC`
    )
    .all(tokenId);
  return rows.map(fromRow);
}

export function getView(
  id: number,
  tokenId: number,
  db: Db = getDb()
): SavedView | null {
  const row = db
    .prepare<[number, number], ViewRow | undefined>(
      `SELECT id, token_id, name, payload, created_at, updated_at
         FROM saved_views
        WHERE id = ? AND token_id = ?`
    )
    .get(id, tokenId);
  return row ? fromRow(row) : null;
}

/**
 * Create a new view. Throws if a view with the same name already
 * exists for this token (the table has a UNIQUE constraint).
 */
export function createView(
  tokenId: number,
  name: string,
  payload: unknown,
  db: Db = getDb()
): SavedView {
  const result = db
    .prepare(
      `INSERT INTO saved_views (token_id, name, payload)
       VALUES (?, ?, ?)`
    )
    .run(tokenId, name, JSON.stringify(payload));
  const id = Number(result.lastInsertRowid);
  const fresh = getView(id, tokenId, db);
  if (!fresh) throw new Error("createView: row vanished after insert");
  return fresh;
}

/**
 * Update an existing view's name and/or payload. Both fields are
 * optional; passing undefined leaves the column unchanged. Returns
 * the updated row, or null if the view doesn't belong to this token.
 */
export function updateView(
  id: number,
  tokenId: number,
  patch: { name?: string; payload?: unknown },
  db: Db = getDb()
): SavedView | null {
  const existing = getView(id, tokenId, db);
  if (!existing) return null;
  const name = patch.name !== undefined ? patch.name : existing.name;
  const payload =
    patch.payload !== undefined
      ? JSON.stringify(patch.payload)
      : JSON.stringify(existing.payload);
  db.prepare(
    `UPDATE saved_views
        SET name = ?, payload = ?, updated_at = datetime('now')
      WHERE id = ? AND token_id = ?`
  ).run(name, payload, id, tokenId);
  return getView(id, tokenId, db);
}

export function deleteView(
  id: number,
  tokenId: number,
  db: Db = getDb()
): boolean {
  const r = db
    .prepare(`DELETE FROM saved_views WHERE id = ? AND token_id = ?`)
    .run(id, tokenId);
  return r.changes > 0;
}
