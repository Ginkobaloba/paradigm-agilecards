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

  // Schema v3: named saved views per token. A view is
  // (filters + sort + grouping), serialized as JSON in `payload`. Keyed
  // by token_id today (the token is our user proxy until proper
  // accounts land). Views are shareable via URL-encoded payload, which
  // is separate from this storage path.
  db.exec(`
    CREATE TABLE IF NOT EXISTS saved_views (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      token_id    INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
      name        TEXT    NOT NULL,
      payload     TEXT    NOT NULL,
      created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
      UNIQUE (token_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_saved_views_token
      ON saved_views (token_id);
  `);
  db.prepare(
    `INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)`
  ).run(3);

  // Schema v4: per-card lifecycle event log. The chokidar watcher derives
  // these from frontmatter diffs (events/derive.ts) and the card detail
  // modal renders them as a timeline. Details column is opaque JSON.
  db.exec(`
    CREATE TABLE IF NOT EXISTS card_events (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      card_id     TEXT    NOT NULL,
      type        TEXT    NOT NULL,
      at          TEXT    NOT NULL,
      details     TEXT,
      created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_card_events_card_at
      ON card_events (card_id, at);
  `);
  db.prepare(
    `INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)`
  ).run(4);

  // Schema v5: extend sprints with status + capacity targets + a
  // soft-delete marker. Existing rows get sensible defaults via DEFAULT
  // and a one-time backfill UPDATE. ALTER TABLE in SQLite can only ADD
  // COLUMN with a constant default; no CHECK constraints on the added
  // column (we validate at the route layer instead).
  if (!hasColumn(db, "sprints", "status")) {
    db.exec(
      `ALTER TABLE sprints ADD COLUMN status TEXT NOT NULL DEFAULT 'planning'`
    );
  }
  if (!hasColumn(db, "sprints", "points_target")) {
    db.exec(`ALTER TABLE sprints ADD COLUMN points_target INTEGER`);
  }
  if (!hasColumn(db, "sprints", "dollar_target")) {
    db.exec(`ALTER TABLE sprints ADD COLUMN dollar_target REAL`);
  }
  if (!hasColumn(db, "sprints", "review_hours_target")) {
    db.exec(`ALTER TABLE sprints ADD COLUMN review_hours_target REAL`);
  }
  if (!hasColumn(db, "sprints", "archived_at")) {
    db.exec(`ALTER TABLE sprints ADD COLUMN archived_at TEXT`);
  }
  db.prepare(
    `INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)`
  ).run(5);
}

interface ColumnInfoRow {
  name: string;
}

function hasColumn(db: Db, table: string, column: string): boolean {
  // PRAGMA table_info doesn't take bound parameters; the table name is
  // ours (not user-supplied), so safe to interpolate.
  const rows = db
    .prepare(`PRAGMA table_info(${table})`)
    .all() as ColumnInfoRow[];
  return rows.some((r) => r.name === column);
}
