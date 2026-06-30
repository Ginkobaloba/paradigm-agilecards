/**
 * Tests for manual-rank storage. Uses an in-memory better-sqlite3 DB
 * so we never touch the production data file.
 */

import { strict as assert } from "node:assert";
import { describe, it, beforeEach } from "node:test";
import Database from "better-sqlite3";

import {
  appendRank,
  getAllRanks,
  getRank,
  maxRankInStatus,
  RANK_BASE,
  RANK_STEP,
  removeRank,
  setRankBetween,
  upsertRank,
} from "./ranks.js";

function mkDb(): Database.Database {
  const db = new Database(":memory:");
  db.pragma("foreign_keys = ON");
  db.exec(`
    CREATE TABLE card_rank (
      card_id    TEXT    PRIMARY KEY,
      status     TEXT    NOT NULL,
      rank       REAL    NOT NULL,
      updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_card_rank_status_rank ON card_rank (status, rank);
  `);
  return db;
}

describe("ranks", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = mkDb();
  });

  it("upsertRank persists and getRank reads back", () => {
    upsertRank("c1", "active", 500, db);
    const row = getRank("c1", db);
    assert.equal(row?.status, "active");
    assert.equal(row?.rank, 500);
  });

  it("upsertRank replaces an existing row", () => {
    upsertRank("c1", "active", 500, db);
    upsertRank("c1", "done", 100, db);
    const row = getRank("c1", db);
    assert.equal(row?.status, "done");
    assert.equal(row?.rank, 100);
  });

  it("maxRankInStatus returns null when empty", () => {
    assert.equal(maxRankInStatus("active", db), null);
  });

  it("maxRankInStatus returns the largest rank in that status only", () => {
    upsertRank("a", "active", 1024, db);
    upsertRank("b", "active", 2048, db);
    upsertRank("c", "done", 9999, db);
    assert.equal(maxRankInStatus("active", db), 2048);
  });

  it("appendRank places the first card at RANK_BASE", () => {
    const r = appendRank("first", "backlog", db);
    assert.equal(r, RANK_BASE);
  });

  it("appendRank steps past the existing max in the column", () => {
    upsertRank("a", "backlog", 500, db);
    const r = appendRank("b", "backlog", db);
    assert.equal(r, 500 + RANK_STEP);
  });

  it("setRankBetween midpoints when both neighbors are present", () => {
    upsertRank("prev", "active", 100, db);
    upsertRank("next", "active", 300, db);
    const r = setRankBetween("c", "active", "prev", "next", db);
    assert.equal(r, 200);
    assert.equal(getRank("c", db)?.rank, 200);
  });

  it("setRankBetween appends when only prev is provided", () => {
    upsertRank("prev", "active", 500, db);
    const r = setRankBetween("c", "active", "prev", null, db);
    assert.equal(r, 500 + RANK_STEP);
  });

  it("setRankBetween prepends when only next is provided", () => {
    upsertRank("next", "active", 500, db);
    const r = setRankBetween("c", "active", null, "next", db);
    assert.equal(r, 500 - RANK_STEP);
  });

  it("setRankBetween falls back to base when neither neighbor exists", () => {
    const r = setRankBetween("c", "active", null, null, db);
    assert.equal(r, RANK_BASE);
  });

  it("setRankBetween treats unknown neighbor ids as missing", () => {
    const r = setRankBetween("c", "active", "ghost-prev", "ghost-next", db);
    assert.equal(r, RANK_BASE);
  });

  it("removeRank deletes the row", () => {
    upsertRank("c", "active", 500, db);
    removeRank("c", db);
    assert.equal(getRank("c", db), null);
  });

  it("getAllRanks returns every persisted row", () => {
    upsertRank("a", "active", 100, db);
    upsertRank("b", "backlog", 200, db);
    const rows = getAllRanks(db).sort((x, y) => x.cardId.localeCompare(y.cardId));
    assert.equal(rows.length, 2);
    assert.equal(rows[0]?.cardId, "a");
    assert.equal(rows[1]?.cardId, "b");
  });

  it("repeatedly inserting between the same two neighbors keeps producing valid (decreasing) gaps", () => {
    upsertRank("L", "active", 0, db);
    upsertRank("R", "active", 1024, db);
    let leftId = "L";
    for (let i = 0; i < 20; i++) {
      const id = `n${i}`;
      const r = setRankBetween(id, "active", leftId, "R", db);
      const lhs = getRank(leftId, db)!.rank;
      const rhs = getRank("R", db)!.rank;
      assert.ok(r > lhs && r < rhs, `iter ${i}: ${lhs} < ${r} < ${rhs}`);
      leftId = id;
    }
  });
});
