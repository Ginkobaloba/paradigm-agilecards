/**
 * Tests for the stories submit/approve/cancel route.
 *
 * Strategy:
 *   - Use Node's built-in test runner via tsx loader. No new dev deps.
 *   - Spin up an isolated tmpdir, point CARDS_DIR at it before any
 *     dashboard module loads, then dynamic-import the route.
 *   - Inject a fake invoker that writes a known manifest into the
 *     staging dir synchronously. We don't try to shell out to claude
 *     in CI -- the spec explicitly says mock it.
 *   - Walk an Express app instance through submit (parse SSE),
 *     approve (verify files land in backlog/), cancel (verify staging
 *     dir is removed).
 */

import test, { describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";

// Set env BEFORE any dashboard import. The config module reads env at
// load time, and our staging code derives the staging path from config.
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "stories-test-"));
process.env["CARDS_DIR"] = tmpRoot;
process.env["DB_PATH"] = path.join(tmpRoot, "test.sqlite");
process.env["LOG_LEVEL"] = "error";
// Random port -- the supertest pattern would be lighter, but we don't
// have supertest as a dep. We just listen on :0 and read the assigned
// port from the server.
process.env["PORT"] = "0";

// Dynamic imports so the env above is in place first.
const express = (await import("express")).default;
const { storiesRouter } = await import("./stories.js");
const { stagingDirFor, backlogDir, batchesDirFor } = await import(
  "../stories/staging.js"
);
const { ManifestModule } = { ManifestModule: await import("../stories/manifest.js") };
type Manifest = import("../stories/manifest.js").Manifest;
type Invoker = import("../stories/invoker.js").Invoker;

void ManifestModule; // kept to ensure module is reachable from this file

/**
 * Fake invoker. Writes two .md files + a manifest.json into the
 * staging dir, then emits a handful of progress events.
 */
function makeFakeInvoker(opts: { fail?: boolean } = {}): Invoker {
  return async (invokeOpts, onProgress) => {
    onProgress({
      step: "planning",
      agent: "planner",
      message: "decomposing story",
    });
    onProgress({
      step: "review",
      agent: "reviewer",
      message: "checking for hidden coupling",
    });

    if (opts.fail) {
      throw new Error("simulated planner failure");
    }

    const staging = stagingDirFor(invokeOpts.batchId);
    fs.mkdirSync(staging, { recursive: true });
    const card1 = [
      "---",
      `id: ${invokeOpts.batchId}-01-card-one`,
      "title: Card One",
      "status: backlog",
      "points: 2",
      "model: claude-sonnet-4-6",
      "estimated_tokens: 8000",
      "depends_on: []",
      "---",
      "",
      "Body one.",
      "",
    ].join("\n");
    const card2 = [
      "---",
      `id: ${invokeOpts.batchId}-02-card-two`,
      "title: Card Two",
      "status: backlog",
      "points: 4",
      "model: claude-opus-4-7",
      "estimated_tokens: 22000",
      `depends_on:`,
      `  - ${invokeOpts.batchId}-01-card-one`,
      "---",
      "",
      "Body two.",
      "",
    ].join("\n");
    fs.writeFileSync(
      path.join(staging, `${invokeOpts.batchId}-01-card-one.md`),
      card1
    );
    fs.writeFileSync(
      path.join(staging, `${invokeOpts.batchId}-02-card-two.md`),
      card2
    );
    const manifest = {
      batchId: invokeOpts.batchId,
      cards: [
        {
          id: `${invokeOpts.batchId}-01-card-one`,
          title: "Card One",
          file: `${invokeOpts.batchId}-01-card-one.md`,
          tier: 2,
          model: "claude-sonnet-4-6",
          estimated_tokens: 8000,
          depends_on: [],
        },
        {
          id: `${invokeOpts.batchId}-02-card-two`,
          title: "Card Two",
          file: `${invokeOpts.batchId}-02-card-two.md`,
          tier: 4,
          model: "claude-opus-4-7",
          estimated_tokens: 22000,
          depends_on: [`${invokeOpts.batchId}-01-card-one`],
        },
      ],
    };
    fs.writeFileSync(
      path.join(staging, "manifest.json"),
      JSON.stringify(manifest, null, 2)
    );

    return {
      manifest: {
        batchId: invokeOpts.batchId,
        story: invokeOpts.story,
        projectPath: invokeOpts.projectPath,
        mode: invokeOpts.mode,
        deepPlanning: invokeOpts.deepPlanning,
        cards: manifest.cards.map((c) => ({
          id: c.id,
          title: c.title,
          file: c.file,
          tier: c.tier,
          model: c.model,
          estimatedTokens: c.estimated_tokens,
          dependsOn: c.depends_on,
        })),
        histogram: { "2": 1, "4": 1 },
        dependsOnEdges: 1,
        claimableCount: 1,
      } satisfies Manifest,
    };
  };
}

interface ServerHandle {
  url: string;
  close: () => Promise<void>;
}

async function startTestServer(invoker: Invoker): Promise<ServerHandle> {
  const app = express();
  app.use(express.json({ limit: "256kb" }));
  app.use("/api", storiesRouter({ invoker }));
  return new Promise<ServerHandle>((resolve, reject) => {
    const server = app.listen(0, () => {
      const addr = server.address();
      if (!addr || typeof addr === "string") {
        reject(new Error("could not get test server address"));
        return;
      }
      resolve({
        url: `http://127.0.0.1:${addr.port}`,
        close: () =>
          new Promise<void>((res) => {
            server.close(() => res());
          }),
      });
    });
  });
}

interface SseEvent {
  event: string;
  data: unknown;
}

/**
 * POST a JSON body and consume an SSE response, returning every parsed
 * `event:`/`data:` pair until the server closes the connection.
 */
async function postSse(
  url: string,
  body: unknown
): Promise<{ status: number; events: SseEvent[] }> {
  const u = new URL(url);
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = http.request(
      {
        method: "POST",
        hostname: u.hostname,
        port: u.port,
        path: u.pathname + u.search,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload).toString(),
          Accept: "text/event-stream",
        },
      },
      (res) => {
        const status = res.statusCode ?? 0;
        let buf = "";
        res.setEncoding("utf8");
        const events: SseEvent[] = [];
        res.on("data", (chunk: string) => {
          buf += chunk;
          let idx = buf.indexOf("\n\n");
          while (idx !== -1) {
            const block = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const parsed = parseSseBlock(block);
            if (parsed) events.push(parsed);
            idx = buf.indexOf("\n\n");
          }
        });
        res.on("end", () => resolve({ status, events }));
        res.on("error", reject);
      }
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

function parseSseBlock(block: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  let data: unknown = raw;
  try {
    data = JSON.parse(raw);
  } catch {
    /* keep raw string */
  }
  return { event, data };
}

async function postJson(
  url: string,
  body: unknown
): Promise<{ status: number; body: unknown }> {
  const u = new URL(url);
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? "" : JSON.stringify(body);
    const req = http.request(
      {
        method: "POST",
        hostname: u.hostname,
        port: u.port,
        path: u.pathname + u.search,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload).toString(),
        },
      },
      (res) => {
        let buf = "";
        res.setEncoding("utf8");
        res.on("data", (c: string) => (buf += c));
        res.on("end", () => {
          let parsed: unknown = buf;
          try {
            parsed = JSON.parse(buf);
          } catch {
            /* keep raw */
          }
          resolve({ status: res.statusCode ?? 0, body: parsed });
        });
      }
    );
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

describe("POST /api/stories/submit", () => {
  test("rejects empty body", async () => {
    const srv = await startTestServer(makeFakeInvoker());
    try {
      const res = await postJson(`${srv.url}/api/stories/submit`, {});
      assert.equal(res.status, 400);
      assert.ok(
        typeof res.body === "object" &&
          res.body !== null &&
          "error" in res.body
      );
    } finally {
      await srv.close();
    }
  });

  test("streams progress, dry_run, then closes; staged batch is approvable", async () => {
    const srv = await startTestServer(makeFakeInvoker());
    try {
      const { status, events } = await postSse(`${srv.url}/api/stories/submit`, {
        story: "As an operator I want to rate-limit the public API.",
        project_path: "C:\\dev\\project-x",
        mode: "full",
        deep_planning: false,
      });
      assert.equal(status, 200);
      const eventNames = events.map((e) => e.event);
      assert.ok(eventNames.includes("progress"), `events: ${eventNames.join(",")}`);
      assert.ok(eventNames.includes("dry_run"), `events: ${eventNames.join(",")}`);
      assert.ok(!eventNames.includes("error"));

      const dryRun = events.find((e) => e.event === "dry_run");
      assert.ok(dryRun, "expected a dry_run event");
      const data = dryRun.data as {
        batch_id: string;
        cards: Array<{ id: string; tier: number | null; dependsOn?: string[] }>;
        histogram: Record<string, number>;
        depends_on_edges: number;
        claimable_count: number;
      };
      assert.equal(typeof data.batch_id, "string");
      assert.equal(data.cards.length, 2);
      assert.equal(data.depends_on_edges, 1);
      assert.equal(data.claimable_count, 1);

      // Approve.
      const approved = await postJson(
        `${srv.url}/api/stories/${data.batch_id}/approve`,
        undefined
      );
      assert.equal(approved.status, 200);
      const approvedBody = approved.body as {
        batchId: string;
        cardsWritten: number;
      };
      assert.equal(approvedBody.cardsWritten, 2);

      // Files now in backlog/.
      const backlog = fs.readdirSync(backlogDir()).filter((f) => f.endsWith(".md"));
      assert.equal(backlog.length, 2);

      // Manifest promoted into _batches/<id>/.
      const manifestPath = path.join(
        batchesDirFor(data.batch_id),
        "manifest.json"
      );
      assert.ok(fs.existsSync(manifestPath), `expected manifest at ${manifestPath}`);

      // Staging dir gone.
      assert.equal(fs.existsSync(stagingDirFor(data.batch_id)), false);
    } finally {
      await srv.close();
    }
  });

  test("cancel removes the staging dir and refuses approve afterward", async () => {
    const srv = await startTestServer(makeFakeInvoker());
    try {
      const { events } = await postSse(`${srv.url}/api/stories/submit`, {
        story: "Some other story big enough to plan against.",
      });
      const dryRun = events.find((e) => e.event === "dry_run");
      assert.ok(dryRun);
      const batchId = (dryRun.data as { batch_id: string }).batch_id;

      const cancelled = await postJson(
        `${srv.url}/api/stories/${batchId}/cancel`,
        undefined
      );
      assert.equal(cancelled.status, 200);
      assert.equal(fs.existsSync(stagingDirFor(batchId)), false);

      const tryApprove = await postJson(
        `${srv.url}/api/stories/${batchId}/approve`,
        undefined
      );
      assert.equal(tryApprove.status, 404);
    } finally {
      await srv.close();
    }
  });

  test("emits error event when the invoker throws", async () => {
    const srv = await startTestServer(makeFakeInvoker({ fail: true }));
    try {
      const { events } = await postSse(`${srv.url}/api/stories/submit`, {
        story: "story body",
      });
      const err = events.find((e) => e.event === "error");
      assert.ok(err, `events: ${events.map((e) => e.event).join(",")}`);
      const data = err.data as { stage: string; message: string };
      assert.equal(data.stage, "planning");
      assert.match(data.message, /simulated/);
    } finally {
      await srv.close();
    }
  });
});
