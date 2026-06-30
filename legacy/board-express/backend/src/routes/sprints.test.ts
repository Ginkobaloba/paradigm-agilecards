/**
 * Tests for the sprints route. Spins up an Express app on :0 with an
 * isolated CARDS_DIR and DB_PATH and exercises every route via fetch.
 *
 * Uses the same dynamic-import-after-env pattern as stories.test.ts so
 * the dashboard's config module reads the test env at first import.
 */

import test, { describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "sprints-test-"));
process.env["CARDS_DIR"] = tmpRoot;
process.env["DB_PATH"] = path.join(tmpRoot, "test.sqlite");
process.env["LOG_LEVEL"] = "error";
process.env["PORT"] = "0";

const express = (await import("express")).default;
const { sprintsRouter } = await import("./sprints.js");
const { getDb } = await import("../db/sqlite.js");

function resetDb(): void {
  // Wipe sprint tables between tests so the route assertions can rely
  // on a known empty starting state. We touch only the tables this file
  // exercises -- other test files (stories, ranks, ...) keep their
  // tables intact.
  const db = getDb();
  db.exec("DELETE FROM sprint_cards");
  db.exec("DELETE FROM sprints");
}

interface ServerHandle {
  url: string;
  close: () => Promise<void>;
}

async function startTestServer(): Promise<ServerHandle> {
  const app = express();
  app.use(express.json({ limit: "256kb" }));
  app.use("/api", sprintsRouter());
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
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  return { status: res.status, json };
}

describe("sprints route", () => {
  beforeEach(() => {
    resetDb();
  });

  test("POST /sprints creates a sprint with camelCase shape", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "Sprint 1",
        startsAt: "2026-06-01",
        endsAt: "2026-06-14",
        goal: "Ship the planner UI",
      });
      assert.equal(r.status, 201);
      const sprint = (r.json as { sprint: Record<string, unknown> }).sprint;
      assert.equal(sprint["name"], "Sprint 1");
      assert.equal(sprint["startsAt"], "2026-06-01");
      assert.equal(sprint["endsAt"], "2026-06-14");
      assert.equal(sprint["goal"], "Ship the planner UI");
      assert.equal(sprint["status"], "planning");
      assert.equal(sprint["pointsTarget"], null);
      assert.equal(sprint["archivedAt"], null);
      assert.ok(typeof sprint["createdAt"] === "string");
    } finally {
      await srv.close();
    }
  });

  test("POST /sprints validates required fields", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "",
        startsAt: "2026-06-01",
        endsAt: "2026-06-14",
      });
      assert.equal(r.status, 400);
    } finally {
      await srv.close();
    }
  });

  test("POST /sprints rejects endsAt before startsAt", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "Bad dates",
        startsAt: "2026-06-14",
        endsAt: "2026-06-01",
      });
      assert.equal(r.status, 400);
    } finally {
      await srv.close();
    }
  });

  test("GET /sprints returns the list with rollups", async () => {
    const srv = await startTestServer();
    try {
      const c1 = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "S1",
        startsAt: "2026-06-01",
        endsAt: "2026-06-14",
      });
      const id1 = (c1.json as { sprint: { id: number } }).sprint.id;
      const c2 = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "S2",
        startsAt: "2026-06-15",
        endsAt: "2026-06-28",
      });
      const id2 = (c2.json as { sprint: { id: number } }).sprint.id;

      await jsonReq("POST", `${srv.url}/api/sprints/${id1}/cards`, {
        cardId: "card-a",
        plannedPoints: 3,
      });
      await jsonReq("POST", `${srv.url}/api/sprints/${id1}/cards`, {
        cardId: "card-b",
        plannedPoints: 5,
      });

      const r = await jsonReq("GET", `${srv.url}/api/sprints`);
      assert.equal(r.status, 200);
      const list = (r.json as { sprints: Array<Record<string, unknown>> })
        .sprints;
      // Most-recent first (descending starts_at): S2 then S1.
      assert.equal(list[0]?.["id"], id2);
      assert.equal(list[0]?.["cardCount"], 0);
      assert.equal(list[0]?.["plannedPointsSum"], 0);
      assert.equal(list[1]?.["id"], id1);
      assert.equal(list[1]?.["cardCount"], 2);
      assert.equal(list[1]?.["plannedPointsSum"], 8);
    } finally {
      await srv.close();
    }
  });

  test("GET /sprints hides archived sprints by default; includeArchived=1 surfaces them", async () => {
    const srv = await startTestServer();
    try {
      const c = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "to-archive",
        startsAt: "2026-06-01",
        endsAt: "2026-06-14",
      });
      const id = (c.json as { sprint: { id: number } }).sprint.id;
      await jsonReq("PATCH", `${srv.url}/api/sprints/${id}`, {
        archivedAt: new Date().toISOString(),
      });

      const r = await jsonReq("GET", `${srv.url}/api/sprints`);
      assert.equal(
        (r.json as { sprints: unknown[] }).sprints.length,
        0,
        "default list should hide archived"
      );

      const r2 = await jsonReq(
        "GET",
        `${srv.url}/api/sprints?includeArchived=1`
      );
      assert.equal(
        (r2.json as { sprints: unknown[] }).sprints.length,
        1,
        "includeArchived should surface the archived sprint"
      );
    } finally {
      await srv.close();
    }
  });

  test("PATCH /sprints/:id updates only the fields supplied", async () => {
    const srv = await startTestServer();
    try {
      const c = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "Original",
        startsAt: "2026-06-01",
        endsAt: "2026-06-14",
        goal: "do thing",
      });
      const id = (c.json as { sprint: { id: number } }).sprint.id;

      const p = await jsonReq("PATCH", `${srv.url}/api/sprints/${id}`, {
        status: "active",
        pointsTarget: 25,
        dollarTarget: 100,
        reviewHoursTarget: 4,
      });
      assert.equal(p.status, 200);
      const updated = (p.json as { sprint: Record<string, unknown> }).sprint;
      assert.equal(updated["status"], "active");
      assert.equal(updated["pointsTarget"], 25);
      assert.equal(updated["dollarTarget"], 100);
      assert.equal(updated["reviewHoursTarget"], 4);
      // unchanged fields remain
      assert.equal(updated["name"], "Original");
      assert.equal(updated["goal"], "do thing");
    } finally {
      await srv.close();
    }
  });

  test("PATCH /sprints/:id rejects unknown status", async () => {
    const srv = await startTestServer();
    try {
      const c = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "x",
        startsAt: "2026-06-01",
        endsAt: "2026-06-14",
      });
      const id = (c.json as { sprint: { id: number } }).sprint.id;
      const r = await jsonReq("PATCH", `${srv.url}/api/sprints/${id}`, {
        status: "bogus",
      });
      assert.equal(r.status, 400);
    } finally {
      await srv.close();
    }
  });

  test("DELETE /sprints/:id/cards/:cardId removes the membership", async () => {
    const srv = await startTestServer();
    try {
      const c = await jsonReq("POST", `${srv.url}/api/sprints`, {
        name: "S",
        startsAt: "2026-06-01",
        endsAt: "2026-06-14",
      });
      const id = (c.json as { sprint: { id: number } }).sprint.id;
      await jsonReq("POST", `${srv.url}/api/sprints/${id}/cards`, {
        cardId: "card-a",
        plannedPoints: 3,
      });
      const before = await jsonReq("GET", `${srv.url}/api/sprints/${id}`);
      assert.equal(
        (before.json as { cards: unknown[] }).cards.length,
        1
      );

      const del = await jsonReq(
        "DELETE",
        `${srv.url}/api/sprints/${id}/cards/card-a`
      );
      assert.equal(del.status, 204);

      const after = await jsonReq("GET", `${srv.url}/api/sprints/${id}`);
      assert.equal(
        (after.json as { cards: unknown[] }).cards.length,
        0
      );
    } finally {
      await srv.close();
    }
  });

  test("GET /sprints/:id returns 404 for an unknown id", async () => {
    const srv = await startTestServer();
    try {
      const r = await jsonReq("GET", `${srv.url}/api/sprints/9999`);
      assert.equal(r.status, 404);
    } finally {
      await srv.close();
    }
  });
});
