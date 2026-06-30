/**
 * Tests for the triage inbox routes (roadmap 2.4): list staged batches,
 * promote / decline / merge a single staged card. The load-bearing
 * properties: nothing the planner produced is destroyed (decline and
 * merge park files under _declined/), promote refuses to overwrite an
 * existing backlog card, a drained batch finalizes its manifest into
 * _batches/, and the file-name guard blocks anything that could escape
 * the staging tree.
 *
 * Setup discipline matches cards.test.ts: tmpdir CARDS_DIR before any
 * dashboard module loads, dynamic-import the routers, run
 * startWatcher() to populate the index (merge needs it), close the
 * watcher so chokidar doesn't race the assertions.
 */

import test, { describe, before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "triage-test-"));
process.env["CARDS_DIR"] = tmpRoot;
process.env["DB_PATH"] = path.join(tmpRoot, "test.sqlite");
process.env["LOG_LEVEL"] = "error";
process.env["PORT"] = "0";

const express = (await import("express")).default;
const { triageRouter } = await import("./triage.js");
const cardsFs = await import("../fs/cards.js");

interface ServerHandle {
  url: string;
  close: () => Promise<void>;
}

async function startTestServer(): Promise<ServerHandle> {
  const app = express();
  app.use(express.json({ limit: "256kb" }));
  app.use("/api", triageRouter());
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
    headers:
      body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  return { status: res.status, json };
}

function cardText(id: string, title: string, body = "The body."): string {
  return [
    "---",
    `id: ${id}`,
    `title: ${title}`,
    "status: backlog",
    "points: 2",
    "model: claude-sonnet-4-6",
    "estimated_tokens: 500000",
    "---",
    "",
    body,
    "",
  ].join("\n");
}

function seedStagedCard(
  batchId: string,
  fileName: string,
  id: string,
  title: string,
  body = "Staged body text."
): string {
  const dir = path.join(tmpRoot, "_staging", batchId);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, fileName);
  fs.writeFileSync(file, cardText(id, title, body), "utf8");
  return file;
}

function seedManifest(batchId: string, story: string): void {
  const dir = path.join(tmpRoot, "_staging", batchId);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, "manifest.json"),
    JSON.stringify({ story, cards: [] }),
    "utf8"
  );
}

function seedBacklogCard(id: string, title: string): string {
  const file = path.join(tmpRoot, "backlog", `${id}.md`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, cardText(id, title, "Existing body."), "utf8");
  return file;
}

describe("triage inbox routes", () => {
  let watcher: import("chokidar").FSWatcher;
  let server: ServerHandle;

  before(async () => {
    fs.mkdirSync(path.join(tmpRoot, "backlog"), { recursive: true });
    seedBacklogCard("existing-01", "Existing rate limiting card");
    watcher = cardsFs.startWatcher();
    await watcher.close();
    server = await startTestServer();
  });

  after(async () => {
    await server.close();
    try {
      fs.rmSync(tmpRoot, { recursive: true, force: true });
    } catch {
      // better-sqlite3 keeps test.sqlite open (getDb() is never
      // closed), so Windows reports EBUSY here. The OS temp dir is
      // disposable; matching cards.test.ts.
    }
  });

  beforeEach(() => {
    // Each test seeds its own batch; clear leftovers between tests.
    fs.rmSync(path.join(tmpRoot, "_staging"), {
      recursive: true,
      force: true,
    });
    fs.rmSync(path.join(tmpRoot, "_declined"), {
      recursive: true,
      force: true,
    });
  });

  test("GET /api/triage lists staged cards with excerpt and estimate", async () => {
    seedStagedCard("b100", "b100-01-one.md", "b100-01", "Add rate limit");
    seedStagedCard("b100", "b100-02-two.md", "b100-02", "Add retry logic");
    seedManifest("b100", "A story about resilience");

    const { status, json } = await jsonReq("GET", `${server.url}/api/triage`);
    assert.equal(status, 200);
    const payload = json as {
      batches: Array<{
        batchId: string;
        story: string | null;
        cards: Array<Record<string, unknown>>;
      }>;
    };
    assert.equal(payload.batches.length, 1);
    const batch = payload.batches[0]!;
    assert.equal(batch.batchId, "b100");
    assert.equal(batch.story, "A story about resilience");
    assert.equal(batch.cards.length, 2);
    const first = batch.cards[0]!;
    assert.equal(first["id"], "b100-01");
    assert.equal(first["title"], "Add rate limit");
    assert.equal(first["bodyExcerpt"], "Staged body text.");
    assert.equal(first["tier"], 2);
    assert.equal(first["estimatedTokens"], 500000);
  });

  test("GET /api/triage is empty when nothing staged", async () => {
    const { status, json } = await jsonReq("GET", `${server.url}/api/triage`);
    assert.equal(status, 200);
    assert.deepEqual(json, { batches: [] });
  });

  test("promote moves the file to backlog and assigns a rank", async () => {
    seedStagedCard("b200", "b200-01-card.md", "b200-01", "Promote me");

    const { status, json } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b200/cards/${encodeURIComponent("b200-01-card.md")}/promote`
    );
    assert.equal(status, 200);
    const payload = json as { id: string; status: string; rank: number };
    assert.equal(payload.id, "b200-01");
    assert.equal(payload.status, "backlog");
    assert.equal(typeof payload.rank, "number");
    assert.ok(
      fs.existsSync(path.join(tmpRoot, "backlog", "b200-01-card.md"))
    );
    assert.ok(
      !fs.existsSync(path.join(tmpRoot, "_staging", "b200", "b200-01-card.md"))
    );
  });

  test("promote refuses to overwrite an existing backlog file", async () => {
    seedStagedCard("b201", "clash.md", "clash-01", "Collides");
    fs.writeFileSync(
      path.join(tmpRoot, "backlog", "clash.md"),
      cardText("clash-00", "Already here"),
      "utf8"
    );

    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b201/cards/clash.md/promote`
    );
    assert.equal(status, 409);
    // The staged file is untouched after the refusal.
    assert.ok(
      fs.existsSync(path.join(tmpRoot, "_staging", "b201", "clash.md"))
    );
  });

  test("decline parks the file under _declined, destroying nothing", async () => {
    seedStagedCard("b300", "b300-01-no.md", "b300-01", "Decline me");

    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b300/cards/b300-01-no.md/decline`
    );
    assert.equal(status, 200);
    assert.ok(
      fs.existsSync(path.join(tmpRoot, "_declined", "b300", "b300-01-no.md"))
    );
    assert.ok(!fs.existsSync(path.join(tmpRoot, "_staging", "b300")));
  });

  test("draining a batch archives its manifest into _batches", async () => {
    seedStagedCard("b400", "b400-01-only.md", "b400-01", "Last one");
    seedManifest("b400", "story");

    await jsonReq(
      "POST",
      `${server.url}/api/triage/b400/cards/b400-01-only.md/decline`
    );
    assert.ok(
      fs.existsSync(
        path.join(tmpRoot, "_batches", "b400", "manifest.json")
      )
    );
    assert.ok(!fs.existsSync(path.join(tmpRoot, "_staging", "b400")));
  });

  test("merge absorbs the staged body into the target and declines the file", async () => {
    seedStagedCard(
      "b500",
      "b500-01-dup.md",
      "b500-01",
      "Rate limiting (duplicate)",
      "Duplicate proposal body."
    );

    const { status, json } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b500/cards/b500-01-dup.md/merge`,
      { targetId: "existing-01" }
    );
    assert.equal(status, 200);
    assert.deepEqual(json, { ok: true, targetId: "existing-01" });

    const target = fs.readFileSync(
      path.join(tmpRoot, "backlog", "existing-01.md"),
      "utf8"
    );
    assert.ok(target.includes("## Absorbed from triage (b500-01)"));
    assert.ok(target.includes("Duplicate proposal body."));
    // Frontmatter untouched.
    assert.ok(target.startsWith("---\nid: existing-01"));
    // Staged file parked, not deleted.
    assert.ok(
      fs.existsSync(path.join(tmpRoot, "_declined", "b500", "b500-01-dup.md"))
    );
  });

  test("merge 404s on an unknown target card", async () => {
    seedStagedCard("b501", "b501-01.md", "b501-01", "Orphan merge");
    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b501/cards/b501-01.md/merge`,
      { targetId: "nope-99" }
    );
    assert.equal(status, 404);
    assert.ok(
      fs.existsSync(path.join(tmpRoot, "_staging", "b501", "b501-01.md"))
    );
  });

  test("merge 400s without a targetId", async () => {
    seedStagedCard("b502", "b502-01.md", "b502-01", "No target");
    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b502/cards/b502-01.md/merge`,
      {}
    );
    assert.equal(status, 400);
  });

  test("actions 404 on unknown batch or file", async () => {
    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/ghost/cards/ghost.md/promote`
    );
    assert.equal(status, 404);
  });

  test("a batch still being planned is hidden and untouchable", async () => {
    seedStagedCard("b700", "b700-01.md", "b700-01", "Half written");
    fs.writeFileSync(
      path.join(tmpRoot, "_staging", "b700", ".planning"),
      "",
      "utf8"
    );

    const list = await jsonReq("GET", `${server.url}/api/triage`);
    assert.deepEqual(list.json, { batches: [] });

    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b700/cards/b700-01.md/promote`
    );
    assert.equal(status, 409);
    assert.ok(
      fs.existsSync(path.join(tmpRoot, "_staging", "b700", "b700-01.md"))
    );
  });

  test("declining onto an existing declined file collides into a numbered sibling", async () => {
    fs.mkdirSync(path.join(tmpRoot, "_declined", "b800"), {
      recursive: true,
    });
    fs.writeFileSync(
      path.join(tmpRoot, "_declined", "b800", "b800-01.md"),
      "earlier declined copy",
      "utf8"
    );
    seedStagedCard("b800", "b800-01.md", "b800-01", "Declined twice");

    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b800/cards/b800-01.md/decline`
    );
    assert.equal(status, 200);
    assert.equal(
      fs.readFileSync(
        path.join(tmpRoot, "_declined", "b800", "b800-01.md"),
        "utf8"
      ),
      "earlier declined copy"
    );
    assert.ok(
      fs.existsSync(path.join(tmpRoot, "_declined", "b800", "b800-01.1.md"))
    );
  });

  test("merge retry does not duplicate the absorbed section", async () => {
    seedStagedCard(
      "b900",
      "b900-01.md",
      "b900-01",
      "Retry merge",
      "Body once."
    );
    const first = await jsonReq(
      "POST",
      `${server.url}/api/triage/b900/cards/b900-01.md/merge`,
      { targetId: "existing-01" }
    );
    assert.equal(first.status, 200);

    // Simulate the retry-after-partial-failure path: the staged file
    // reappears (as if decline had failed) and the user merges again.
    seedStagedCard(
      "b900",
      "b900-01.md",
      "b900-01",
      "Retry merge",
      "Body once."
    );
    const second = await jsonReq(
      "POST",
      `${server.url}/api/triage/b900/cards/b900-01.md/merge`,
      { targetId: "existing-01" }
    );
    assert.equal(second.status, 200);

    const target = fs.readFileSync(
      path.join(tmpRoot, "backlog", "existing-01.md"),
      "utf8"
    );
    const occurrences =
      target.split("## Absorbed from triage (b900-01)").length - 1;
    assert.equal(occurrences, 1);
  });

  test("path traversal in the file segment is rejected", async () => {
    seedStagedCard("b600", "b600-01.md", "b600-01", "Innocent");
    // A traversal name never resolves outside staging: the guard 400s
    // before any path is built.
    const evil = encodeURIComponent("..\\..\\backlog\\evil.md");
    const { status } = await jsonReq(
      "POST",
      `${server.url}/api/triage/b600/cards/${evil}/promote`
    );
    assert.equal(status, 400);
    const evil2 = encodeURIComponent("../escape.md");
    const r2 = await jsonReq(
      "POST",
      `${server.url}/api/triage/b600/cards/${evil2}/decline`
    );
    assert.equal(r2.status, 400);
    const notMd = await jsonReq(
      "POST",
      `${server.url}/api/triage/b600/cards/manifest.json/decline`
    );
    assert.equal(notMd.status, 400);
  });
});
