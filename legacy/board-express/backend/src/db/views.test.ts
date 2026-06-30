/**
 * Tests for the saved-view DB layer. In-memory SQLite, schema copy of
 * the production v3 migration's relevant slice.
 */

import { strict as assert } from "node:assert";
import { describe, it, beforeEach } from "node:test";
import Database from "better-sqlite3";

import {
  createView,
  deleteView,
  getView,
  listViews,
  updateView,
} from "./views.js";

function mkDb(): Database.Database {
  const db = new Database(":memory:");
  db.pragma("foreign_keys = ON");
  db.exec(`
    CREATE TABLE tokens (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      label      TEXT    NOT NULL,
      token_hash TEXT    NOT NULL UNIQUE
    );
    CREATE TABLE saved_views (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      token_id    INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
      name        TEXT    NOT NULL,
      payload     TEXT    NOT NULL,
      created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
      UNIQUE (token_id, name)
    );
  `);
  // Seed two tokens so we can prove isolation.
  db.prepare(`INSERT INTO tokens (label, token_hash) VALUES (?, ?)`).run(
    "alice",
    "hash-a"
  );
  db.prepare(`INSERT INTO tokens (label, token_hash) VALUES (?, ?)`).run(
    "bob",
    "hash-b"
  );
  return db;
}

describe("saved views", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = mkDb();
  });

  it("createView round-trips JSON payloads", () => {
    const v = createView(
      1,
      "high-tier sprint",
      { filters: { tier: [4, 5, 6] } },
      db
    );
    assert.equal(v.tokenId, 1);
    assert.equal(v.name, "high-tier sprint");
    assert.deepEqual(v.payload, { filters: { tier: [4, 5, 6] } });
    assert.ok(v.createdAt);
  });

  it("listViews scopes to a single token", () => {
    createView(1, "alice view", { a: 1 }, db);
    createView(2, "bob view", { b: 1 }, db);
    const aliceList = listViews(1, db);
    const bobList = listViews(2, db);
    assert.equal(aliceList.length, 1);
    assert.equal(aliceList[0]?.name, "alice view");
    assert.equal(bobList.length, 1);
    assert.equal(bobList[0]?.name, "bob view");
  });

  it("getView refuses to cross tokens", () => {
    const v = createView(1, "alice", { a: 1 }, db);
    assert.notEqual(getView(v.id, 1, db), null);
    assert.equal(getView(v.id, 2, db), null);
  });

  it("createView rejects duplicate names per token", () => {
    createView(1, "dup", { a: 1 }, db);
    assert.throws(() => createView(1, "dup", { a: 2 }, db));
    // ...but bob is allowed to use the same name.
    const bob = createView(2, "dup", { b: 1 }, db);
    assert.equal(bob.name, "dup");
  });

  it("updateView patches name only", () => {
    const v = createView(1, "old", { x: 1 }, db);
    const u = updateView(v.id, 1, { name: "new" }, db);
    assert.equal(u?.name, "new");
    assert.deepEqual(u?.payload, { x: 1 });
  });

  it("updateView patches payload only", () => {
    const v = createView(1, "v", { x: 1 }, db);
    const u = updateView(v.id, 1, { payload: { y: 2 } }, db);
    assert.equal(u?.name, "v");
    assert.deepEqual(u?.payload, { y: 2 });
  });

  it("updateView returns null when crossing tokens", () => {
    const v = createView(1, "v", { x: 1 }, db);
    assert.equal(updateView(v.id, 2, { name: "hacked" }, db), null);
  });

  it("deleteView is idempotent and token-scoped", () => {
    const v = createView(1, "v", { x: 1 }, db);
    assert.equal(deleteView(v.id, 2, db), false); // wrong token
    assert.equal(deleteView(v.id, 1, db), true);
    assert.equal(deleteView(v.id, 1, db), false); // already gone
  });
});
