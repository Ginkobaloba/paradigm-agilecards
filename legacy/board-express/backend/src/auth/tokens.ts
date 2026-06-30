/**
 * Token store. Mint, validate, list, revoke. Plaintext is only ever
 * returned at mint time; the database holds the hash.
 */

import { getDb } from "../db/sqlite.js";
import { generateTokenPlaintext, sha256Hex } from "./hash.js";

export interface TokenRow {
  readonly id: number;
  readonly label: string;
  readonly tokenHash: string;
  readonly createdAt: string;
  readonly lastUsedAt: string | null;
}

interface TokenRowRaw {
  id: number;
  label: string;
  token_hash: string;
  created_at: string;
  last_used_at: string | null;
}

function row(r: TokenRowRaw): TokenRow {
  return {
    id: r.id,
    label: r.label,
    tokenHash: r.token_hash,
    createdAt: r.created_at,
    lastUsedAt: r.last_used_at,
  };
}

export interface MintedToken {
  readonly id: number;
  readonly label: string;
  readonly plaintext: string;
}

export function mintToken(label: string): MintedToken {
  const plaintext = generateTokenPlaintext();
  const hash = sha256Hex(plaintext);
  const db = getDb();
  const info = db
    .prepare<[string, string]>(
      `INSERT INTO tokens (label, token_hash) VALUES (?, ?)`
    )
    .run(label, hash);
  return {
    id: Number(info.lastInsertRowid),
    label,
    plaintext,
  };
}

export function listTokens(): TokenRow[] {
  const db = getDb();
  const rows = db
    .prepare(
      `SELECT id, label, token_hash, created_at, last_used_at
         FROM tokens
        ORDER BY created_at DESC`
    )
    .all() as TokenRowRaw[];
  return rows.map(row);
}

export function revokeByLabel(label: string): number {
  const db = getDb();
  const info = db.prepare<[string]>(`DELETE FROM tokens WHERE label = ?`).run(label);
  return info.changes;
}

/**
 * Validate a plaintext token. Returns the matching row if valid, null if
 * not. Touches last_used_at as a side effect so we can see when a token
 * was last seen.
 */
export function validateToken(plaintext: string): TokenRow | null {
  if (!plaintext || plaintext.length < 16) return null;
  const hash = sha256Hex(plaintext);
  const db = getDb();
  const r = db
    .prepare<[string]>(
      `SELECT id, label, token_hash, created_at, last_used_at
         FROM tokens
        WHERE token_hash = ?
        LIMIT 1`
    )
    .get(hash) as TokenRowRaw | undefined;
  if (!r) return null;
  db.prepare<[number]>(
    `UPDATE tokens SET last_used_at = datetime('now') WHERE id = ?`
  ).run(r.id);
  return row(r);
}
