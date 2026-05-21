/**
 * SQLite setup. One connection per process; better-sqlite3 is synchronous
 * and serializes inside the binding, which is exactly what we want for a
 * single-host dashboard.
 *
 * Schema is a tiny set of tables for state that doesn't belong in a card:
 *   - tokens: bearer-token store (hash only)
 *   - sprints: planning state (stubbed for v0+, populated in v1)
 *   - retros: retro snapshots (stubbed for v0+, populated in v1)
 *
 * Cards themselves stay on disk. We don't mirror them into SQLite even
 * though we could, because the disk is the source of truth and any
 * mismatch would create a "which one's right" problem with no good answer.
 */

import Database from "better-sqlite3";
import path from "node:path";
import fs from "node:fs";

import { config } from "../config.js";
import { log } from "../logger.js";

export type Db = Database.Database;

let _db: Db | null = null;

export function getDb(): Db {
  if (_db) return _db;

  fs.mkdirSync(path.dirname(config.dbPath), { recursive: true });

  const db = new Database(config.dbPath);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  db.pragma("synchronous = NORMAL");

  migrate(db);

  log.info("sqlite ready", { path: config.dbPath });

  _db = db;
  return _db;
}

function migrate(db: Db): void {
  // Schema v1: the original tables.
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS tokens (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      label        TEXT    NOT NULL,
      token_hash   TEXT    NOT NULL UNIQUE,
      created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
      last_used_at TEXT
    );

    CREATE TABLE IF NOT EXISTS sprints (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      name        TEXT    NOT NULL,
      starts_at   TEXT    NOT NULL,
      ends_at     TEXT    NOT NULL,
      goal        TEXT,
      created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sprint_cards (
      sprint_id  INTEGER NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
      card_id    TEXT    NOT NULL,
      planned_points INTEGER,
      PRIMARY KEY (sprint_id, card_id)
    );

    CREATE TABLE IF NOT EXISTS retros (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      sprint_id   INTEGER REFERENCES sprints(id) ON DELETE SET NULL,
      held_on     TEXT    NOT NULL,
      summary     TEXT,
      created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_tokens_label    ON tokens (label);
    CREATE INDEX IF NOT EXISTS idx_sprint_cards_card ON sprint_cards (card_id);
  `);
  db.prepare(
    `INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)`
  ).run(1);

  // Schema v2: per-card manual rank within a column. Disk file remains
  // the work definition; rank is a presentation concern that lives in
  // SQLite so a drag-reorder doesn't churn frontmatter or git diffs.
  // See roadmap fork A.
  db.exec(`
    CREATE TABLE IF NOT EXISTS card_rank (
      card_id    TEXT    PRIMARY KEY,
      status     TEXT    NOT NULL,
      rank       REAL    NOT NULL,
      updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_card_rank_status_rank
      ON card_rank (status, rank);
  `);
  db.prepare(
    `INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)`
  ).run(2);
}
