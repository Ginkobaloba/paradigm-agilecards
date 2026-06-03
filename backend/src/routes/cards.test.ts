/**
 * Tests for PATCH /api/cards/:id/frontmatter. The endpoint exists for
 * the grid view's drag-to-restake interaction. Two failure modes have
 * to be covered with care: (1) anything outside the whitelist must
 * 400, never reach the rewriter; (2) the disk write has to be atomic
 * across multi-field patches so a half-written file is impossible.
 *
 * Setup discipline matches stories.test.ts: tmpdir CARDS_DIR before any
 * dashboard module loads, dynamic-import the router and the cards FS
 * module, run startWatcher() to populate the in-memory index from the
 * seeded files, then close the watcher to keep chokidar from racing
 * the assertions.
 */

import test, { describe, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cards-patch-test-"));
process.env["CARDS_DIR"] = tmpRoot;
process.env["DB_PATH"] = path.join(tmpRoot, "test.sqlite");
process.env["LOG_LEVEL"] = "error";
process.env["PORT"] = "0";

const express = (await import("express")).default;
const { cardsRouter } = await import("./cards.js");
const cardsFs = await import("../fs/cards.js");

interface ServerHandle {
  url: string;
  close: () => Promise<void>;
}

async function startTestServer(): Promise<ServerHandle> {
  const app = express();
  app.use(express.json({ limit: "256kb" }));
  app.use("/api", cardsRouter());
  return new Promise<ServerHandle>((resolve, reject) => {
    const server = app.listen(0, () => {
      const addr = server.address();
      if (!addr || typeof addr === "string") {
        reject(new Error("no addr"));
        return;
      }
      resolve({
        url: `http://127.0.0.1:${addr.port}`,
        close: () =>
          new Promise<void>((res) => server.close(() => res())),
      });
    });
  });
}

async function jsonReq(
  method: string,
  url: string,
  body?: unknown
): Promise<{ status: number; json: unknown }> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  return { status: res.status, json };
}

function seedCardWithStakes(): string {
  // Card already has stakes + cost_cap_usd, so PATCH exercises the
  // "replace existing line" branch of the rewriter.
  const file = path.join(tmpRoot, "backlog", "test-001.md");
  fs.writeFileSync(
    file,
    [
      "---",
      "id: test-001",
      "title: Test Card One",
      "status: backlog",
      "stakes: low",
      "cost_cap_usd: 0.5",
      "points: 2",
      "---",
      "",
      "Body one.",
      "",
    ].join("\n"),
    "utf8"
  );
  return file;
}

function seedCardWithoutStakes(): string {
  // Card has no stakes / cost_cap_usd, so PATCH exercises the
  // "insert new line" branch of the rewriter.
  const file = path.join(tmpRoot, "backlog", "test-002.md");
  fs.writeFileSync(
    file,
    [
      "---",
      "id: test-002",
      "title: Test Card Two",
      "status: backlog",
      "points: 3",
      "---",
      "",
      "Body two.",
      "",
    ].join("\n"),
    "utf8"
  );
  return file;
}

describe("PATCH /api/cards/:id/frontmatter", () => {
  let watcher: import("chokidar").FSWatcher;
  let cardOneFile: string;
  let cardTwoFile: string;

  before(async () => {
    fs.mkdirSync(path.join(tmpRoot, "backlog"), { recursive: true });
    cardOneFile = seedCardWithStakes();
    cardTwoFile = seedCardWithoutStakes();
    // startWatcher runs the synchronous bootstrap() walk that populates
    // the in-memory index, then attaches a chokidar watcher. We close
    // the watcher immediately so test mutations don't race with
    // chokidar callbacks; the in-memory index stays populated either
    // way.
    watcher = cardsFs.startWatcher();
    await watcher.close();
  });

  after(async () => {
    // Best-effort cleanup; ignore failures because Windows can hold
    // the sqlite file open momentarily after the suite ends.
    try {
      fs.rmSync(tmpRoot, { recursive: true, force: true });
    } catch {
      /* swallow */
    }
  });

  test("happy path: update stakes from low -> high, replaces in place", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-001/frontmatter`,
        { stakes: "high" }
      );
      assert.equal(r.status, 200);
      const card = r.json as { id: string; frontmatter: Record<string, unknown> };
      assert.equal(card.id, "test-001");
      assert.equal(card.frontmatter["stakes"], "high");
      // Disk reflects the change.
      const raw = fs.readFileSync(cardOneFile, "utf8");
      assert.match(raw, /^stakes:\s*high$/m);
      // Other fields are untouched.
      assert.match(raw, /^points:\s*2$/m);
      assert.match(raw, /^title:\s*Test Card One$/m);
    } finally {
      await srv.close();
    }
  });

  test("happy path: update cost_cap_usd to a new positive number", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-001/frontmatter`,
        { cost_cap_usd: 2.5 }
      );
      assert.equal(r.status, 200);
      const card = r.json as { frontmatter: Record<string, unknown> };
      assert.equal(card.frontmatter["cost_cap_usd"], 2.5);
      const raw = fs.readFileSync(cardOneFile, "utf8");
      assert.match(raw, /^cost_cap_usd:\s*2\.5$/m);
    } finally {
      await srv.close();
    }
  });

  test("happy path: patch both fields atomically in one call", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-001/frontmatter`,
        { stakes: "medium", cost_cap_usd: 1.0 }
      );
      assert.equal(r.status, 200);
      const card = r.json as { frontmatter: Record<string, unknown> };
      assert.equal(card.frontmatter["stakes"], "medium");
      assert.equal(card.frontmatter["cost_cap_usd"], 1.0);
    } finally {
      await srv.close();
    }
  });

  test("insert path: add stakes + cost_cap_usd to a card that has neither", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-002/frontmatter`,
        { stakes: "high", cost_cap_usd: 5 }
      );
      assert.equal(r.status, 200);
      const card = r.json as { frontmatter: Record<string, unknown> };
      assert.equal(card.frontmatter["stakes"], "high");
      assert.equal(card.frontmatter["cost_cap_usd"], 5);
      const raw = fs.readFileSync(cardTwoFile, "utf8");
      assert.match(raw, /^stakes:\s*high$/m);
      assert.match(raw, /^cost_cap_usd:\s*5$/m);
      // Original frontmatter is preserved.
      assert.match(raw, /^id:\s*test-002$/m);
      assert.match(raw, /^points:\s*3$/m);
    } finally {
      await srv.close();
    }
  });

  test("null value clears the field line", async () => {
    const srv = await startTestServer();
    try {
      // Prereq state from earlier test: test-002 has stakes=high.
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-002/frontmatter`,
        { stakes: null }
      );
      assert.equal(r.status, 200);
      const card = r.json as { frontmatter: Record<string, unknown> };
      assert.equal(card.frontmatter["stakes"], undefined);
      const raw = fs.readFileSync(cardTwoFile, "utf8");
      assert.doesNotMatch(raw, /^stakes:/m);
    } finally {
      await srv.close();
    }
  });

  test("rejects a field not on the whitelist", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-001/frontmatter`,
        { status: "done" }
      );
      assert.equal(r.status, 400);
      const err = r.json as { error: string };
      assert.match(err.error, /not patchable/);
    } finally {
      await srv.close();
    }
  });

  test("rejects an invalid stakes value", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-001/frontmatter`,
        { stakes: "URGENT" }
      );
      assert.equal(r.status, 400);
      const err = r.json as { error: string };
      assert.match(err.error, /stakes must be one of/);
    } finally {
      await srv.close();
    }
  });

  test("rejects a negative cost_cap_usd", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-001/frontmatter`,
        { cost_cap_usd: -1 }
      );
      assert.equal(r.status, 400);
      const err = r.json as { error: string };
      assert.match(err.error, /positive/);
    } finally {
      await srv.close();
    }
  });

  test("404 for unknown card id", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/no-such-card/frontmatter`,
        { stakes: "high" }
      );
      assert.equal(r.status, 404);
    } finally {
      await srv.close();
    }
  });

  test("preserves trailing # comments and surrounding whitespace on update", async () => {
    const srv = await startTestServer();
    try {
      // Hand-write a card whose stakes line has a trailing comment we
      // expect to survive across a PATCH. Use a fresh id so we don't
      // collide with the cards from earlier tests.
      const file = path.join(tmpRoot, "backlog", "test-comment.md");
      fs.writeFileSync(
        file,
        [
          "---",
          "id: test-comment",
          "title: Comment Preservation",
          "status: backlog",
          "stakes: low   # guessed by the planner",
          "---",
          "",
          "Body.",
          "",
        ].join("\n"),
        "utf8"
      );
      // Re-bootstrap the index since we wrote outside the watcher.
      const watcher2 = cardsFs.startWatcher();
      await watcher2.close();

      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-comment/frontmatter`,
        { stakes: "high" }
      );
      assert.equal(r.status, 200);
      const raw = fs.readFileSync(file, "utf8");
      // The exact spacing and the trailing comment should be intact.
      assert.match(raw, /^stakes: high\s{3}# guessed by the planner$/m);
    } finally {
      await srv.close();
    }
  });

  test("rejects an empty patch body", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq(
        "PATCH",
        `${srv.url}/api/cards/test-001/frontmatter`,
        {}
      );
      assert.equal(r.status, 400);
      const err = r.json as { error: string };
      assert.match(err.error, /empty patch/);
    } finally {
      await srv.close();
    }
  });
});
