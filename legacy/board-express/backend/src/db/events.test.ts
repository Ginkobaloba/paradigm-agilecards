/**
 * Tests for the card_events persistence layer. Uses in-memory sqlite.
 */

import { strict as assert } from "node:assert";
import { describe, it, beforeEach } from "node:test";
import Database from "better-sqlite3";

import {
  appendEvent,
  countEventsForCard,
  getEventsForCard,
} from "./events.js";

function mkDb(): Database.Database {
  const db = new Database(":memory:");
  db.exec(`
    CREATE TABLE card_events (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      card_id     TEXT    NOT NULL,
      type        TEXT    NOT NULL,
      at          TEXT    NOT NULL,
      details     TEXT,
      created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_card_events_card_at ON card_events (card_id, at);
  `);
  return db;
}

describe("card_events store", () => {
  let db: Database.Database;
  beforeEach(() => {
    db = mkDb();
  });

  it("appendEvent persists and getEventsForCard reads back", () => {
    appendEvent(
      {
        cardId: "card-1",
        type: "started",
        at: "2026-05-22T10:00:00.000Z",
        details: { by: "runner-1" },
      },
      db
    );
    const rows = getEventsForCard("card-1", {}, db);
    assert.equal(rows.length, 1);
    assert.equal(rows[0]?.type, "started");
    assert.deepEqual(rows[0]?.details, { by: "runner-1" });
  });

  it("returns events in insertion order regardless of `at` timestamp ordering", () => {
    // Heartbeats can arrive with timestamps slightly out of order from the
    // mtime sequence; insertion order is what the operator actually sees.
    appendEvent(
      { cardId: "c", type: "heartbeat", at: "2026-05-22T10:05:00.000Z" },
      db
    );
    appendEvent(
      { cardId: "c", type: "heartbeat", at: "2026-05-22T10:04:00.000Z" },
      db
    );
    appendEvent(
      { cardId: "c", type: "finished", at: "2026-05-22T10:10:00.000Z" },
      db
    );
    const rows = getEventsForCard("c", {}, db);
    assert.deepEqual(
      rows.map((r) => r.type),
      ["heartbeat", "heartbeat", "finished"]
    );
  });

  it("filters by cardId only", () => {
    appendEvent({ cardId: "c1", type: "discovered", at: "2026-05-22T10:00:00.000Z" }, db);
    appendEvent({ cardId: "c2", type: "discovered", at: "2026-05-22T10:00:00.000Z" }, db);
    appendEvent({ cardId: "c1", type: "started", at: "2026-05-22T10:05:00.000Z" }, db);
    const c1 = getEventsForCard("c1", {}, db);
    assert.equal(c1.length, 2);
    assert.ok(c1.every((r) => r.cardId === "c1"));
  });

  it("countEventsForCard returns the row count", () => {
    assert.equal(countEventsForCard("c", db), 0);
    appendEvent({ cardId: "c", type: "discovered", at: "2026-05-22T10:00:00.000Z" }, db);
    appendEvent({ cardId: "c", type: "started", at: "2026-05-22T10:05:00.000Z" }, db);
    assert.equal(countEventsForCard("c", db), 2);
  });

  it("`since` filter returns only events strictly newer than the given ISO", () => {
    appendEvent({ cardId: "c", type: "discovered", at: "2026-05-22T10:00:00.000Z" }, db);
    appendEvent({ cardId: "c", type: "started", at: "2026-05-22T10:05:00.000Z" }, db);
    appendEvent({ cardId: "c", type: "finished", at: "2026-05-22T10:10:00.000Z" }, db);
    const rows = getEventsForCard(
      "c",
      { since: "2026-05-22T10:05:00.000Z" },
      db
    );
    assert.equal(rows.length, 1);
    assert.equal(rows[0]?.type, "finished");
  });

  it("`limit` caps the result", () => {
    for (let i = 0; i < 5; i++) {
      appendEvent(
        {
          cardId: "c",
          type: "heartbeat",
          at: new Date(Date.UTC(2026, 4, 22, 10, i)).toISOString(),
        },
        db
      );
    }
    const rows = getEventsForCard("c", { limit: 3 }, db);
    assert.equal(rows.length, 3);
  });

  it("hydrates JSON details and tolerates rows with null details", () => {
    appendEvent({ cardId: "c", type: "released", at: "2026-05-22T10:00:00.000Z" }, db);
    appendEvent(
      {
        cardId: "c",
        type: "started",
        at: "2026-05-22T10:01:00.000Z",
        details: { nested: { ok: true, n: 42 } },
      },
      db
    );
    const rows = getEventsForCard("c", {}, db);
    assert.equal(rows[0]?.details, null);
    assert.deepEqual(rows[1]?.details, { nested: { ok: true, n: 42 } });
  });
});
