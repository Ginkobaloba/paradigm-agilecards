/**
 * POST /api/stories/submit -- streamed SSE.
 * POST /api/stories/:batchId/approve -- promotes a staged batch to backlog.
 * POST /api/stories/:batchId/cancel -- discards a staged batch.
 *
 * Lifecycle:
 *
 *   1. Client POSTs `/api/stories/submit` with { story, project_path?,
 *      mode?, deep_planning? }. The connection stays open and the
 *      backend writes SSE events as the planner runs.
 *
 *   2. We allocate a batchId, prepare a staging dir under
 *      `<cardsDir>/_staging/<batchId>/`, and invoke the /cards skill.
 *      Progress events stream as `event: progress`.
 *
 *   3. When the planner finishes we read the staged manifest and emit
 *      `event: dry_run`. The connection then closes. The batch sits in
 *      staging until the human acts.
 *
 *   4. Client POSTs `/api/stories/:batchId/approve` -- backend renames
 *      every staged .md into `<cardsDir>/backlog/`. The chokidar
 *      watcher fires `card-added` events on the live `/events` stream
 *      so the kanban updates in place.
 *
 *   5. Client POSTs `/api/stories/:batchId/cancel` -- backend deletes
 *      the staging dir.
 *
 * Note on the spec's `complete` event: the original task lists a
 * `complete` event on the same SSE stream. We deliver that signal
 * differently: the approve POST returns `{ batchId, cardsWritten }`
 * synchronously, and the per-card SSE arrives on the existing /events
 * channel. Holding the submit stream open across an unknown human
 * delay (review could take minutes) would tie up a worker for no
 * benefit. The frontend coalesces both signals.
 */

import { Router, type Request, type Response } from "express";

import { log } from "../logger.js";
import { generateBatchId, type Invoker, claudeCliInvoker } from "../stories/invoker.js";
import {
  cleanupStaging,
  markStagingReady,
  prepareStaging,
  promoteToBacklog,
  readStagedManifest,
} from "../stories/staging.js";
import type { Manifest } from "../stories/manifest.js";

/** Default invocation timeout. 10 min is enough for a 3-agent deep plan. */
const DEFAULT_TIMEOUT_MS = 10 * 60 * 1000;

/** Max story length we'll accept. The skill itself caps lower; this is the
 * outer guard so a runaway paste can't OOM us. */
const MAX_STORY_BYTES = 64 * 1024;

interface SubmitBody {
  story?: unknown;
  project_path?: unknown;
  mode?: unknown;
  deep_planning?: unknown;
  timeout_ms?: unknown;
}

interface ValidatedSubmit {
  story: string;
  projectPath: string | null;
  mode: "full" | "lean";
  deepPlanning: boolean;
  timeoutMs: number;
}

/**
 * In-memory record of dry-run batches awaiting approval. Lives only in
 * the process; on restart, pending batches are still readable from
 * disk via the staging dir, but the dashboard will treat them as
 * unknown and the operator can promote or remove them by hand. Good
 * enough for v1.
 */
const pending = new Map<string, {
  manifest: Manifest;
  expiresAt: number;
}>();

/** Pending batches age out after an hour. */
const PENDING_TTL_MS = 60 * 60 * 1000;

function reapExpired(): void {
  // The TTL bounds only the in-memory dry-run record. Staged FILES are
  // deliberately left on disk: since the triage inbox (roadmap 2.4)
  // they are the durable pre-backlog lane, resolved per-card via
  // /api/triage promote / merge / decline. Deleting them here (the
  // pre-triage behavior) would silently destroy unreviewed planner
  // output after an hour.
  const now = Date.now();
  for (const [batchId, entry] of pending) {
    if (entry.expiresAt < now) {
      pending.delete(batchId);
    }
  }
}

export interface StoriesRouterDeps {
  /** Injection seam for tests. */
  readonly invoker?: Invoker;
}

export function storiesRouter(deps: StoriesRouterDeps = {}): Router {
  const router = Router();
  const invoker = deps.invoker ?? claudeCliInvoker;

  router.post("/stories/submit", (req: Request, res: Response): void => {
    reapExpired();
    const validation = validateSubmit(req.body as SubmitBody | undefined);
    if ("error" in validation) {
      res.status(400).json({ error: validation.error });
      return;
    }
    runSubmit(invoker, validation, res).catch((err) => {
      log.error("submit handler crashed", { err: String(err) });
      // The SSE writer below already best-effort sends an error event
      // before the stream closes, so there's nothing extra to do here.
    });
  });

  router.post("/stories/:batchId/approve", (req: Request, res: Response): void => {
    reapExpired();
    const batchId = req.params["batchId"];
    if (typeof batchId !== "string" || batchId.length === 0) {
      res.status(400).json({ error: "missing batchId" });
      return;
    }
    const entry = pending.get(batchId);
    if (!entry) {
      res.status(404).json({ error: `no pending batch ${batchId}` });
      return;
    }
    try {
      const { cardsWritten } = promoteToBacklog(batchId);
      pending.delete(batchId);
      res.json({ batchId, cardsWritten });
    } catch (err) {
      res.status(409).json({ error: String(err) });
    }
  });

  router.post("/stories/:batchId/cancel", (req: Request, res: Response): void => {
    const batchId = req.params["batchId"];
    if (typeof batchId !== "string" || batchId.length === 0) {
      res.status(400).json({ error: "missing batchId" });
      return;
    }
    pending.delete(batchId);
    try {
      cleanupStaging(batchId);
    } catch (err) {
      log.warn("cancel cleanup failed", { batchId, err: String(err) });
    }
    res.json({ ok: true });
  });

  router.get("/stories/pending", (_req: Request, res: Response): void => {
    reapExpired();
    const batches = Array.from(pending.entries()).map(([batchId, entry]) => ({
      batchId,
      story: entry.manifest.story.slice(0, 200),
      cardCount: entry.manifest.cards.length,
      expiresAt: new Date(entry.expiresAt).toISOString(),
    }));
    res.json({ pending: batches });
  });

  return router;
}

/**
 * Validate a submit body. Returns a narrowed object or an error string.
 */
function validateSubmit(
  body: SubmitBody | undefined
): ValidatedSubmit | { error: string } {
  if (!body || typeof body !== "object") {
    return { error: "body must be JSON" };
  }
  const storyRaw = body.story;
  if (typeof storyRaw !== "string") {
    return { error: "story is required and must be a string" };
  }
  const story = storyRaw.trim();
  if (story.length === 0) {
    return { error: "story must not be empty" };
  }
  if (Buffer.byteLength(story, "utf8") > MAX_STORY_BYTES) {
    return { error: `story exceeds ${MAX_STORY_BYTES} bytes` };
  }
  const projectPathRaw = body.project_path;
  const projectPath =
    typeof projectPathRaw === "string" && projectPathRaw.trim().length > 0
      ? projectPathRaw.trim()
      : null;

  const modeRaw = body.mode;
  let mode: "full" | "lean" = "full";
  if (modeRaw !== undefined && modeRaw !== null) {
    if (modeRaw !== "full" && modeRaw !== "lean") {
      return { error: "mode must be 'full' or 'lean' when provided" };
    }
    mode = modeRaw;
  }

  const deepPlanningRaw = body.deep_planning;
  const deepPlanning = deepPlanningRaw === true;

  const timeoutRaw = body.timeout_ms;
  let timeoutMs = DEFAULT_TIMEOUT_MS;
  if (typeof timeoutRaw === "number" && Number.isFinite(timeoutRaw)) {
    timeoutMs = Math.max(5_000, Math.min(30 * 60 * 1000, timeoutRaw));
  }

  return { story, projectPath, mode, deepPlanning, timeoutMs };
}

/**
 * Hold open the response, stream SSE events as the invoker runs.
 */
async function runSubmit(
  invoker: Invoker,
  v: ValidatedSubmit,
  res: Response
): Promise<void> {
  const batchId = generateBatchId();

  res.status(200);
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const write = (event: string, data: unknown): void => {
    try {
      res.write(`event: ${event}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    } catch (err) {
      log.warn("sse write failed", { event, err: String(err) });
    }
  };

  // Initial progress so the client knows we're alive.
  write("progress", {
    step: "info",
    agent: "dashboard",
    message: `staging batch ${batchId}`,
    batch_id: batchId,
  });

  try {
    prepareStaging(batchId);
  } catch (err) {
    write("error", { message: String(err), stage: "staging" });
    res.end();
    return;
  }

  try {
    const { manifest } = await invoker(
      {
        story: v.story,
        projectPath: v.projectPath,
        mode: v.mode,
        deepPlanning: v.deepPlanning,
        batchId,
        timeoutMs: v.timeoutMs,
      },
      (e) => {
        write("progress", {
          step: e.step,
          agent: e.agent,
          message: e.message,
          batch_id: batchId,
        });
      }
    );

    // If the invoker didn't write a manifest itself, synthesize one
    // from the staging dir so the dry-run payload is uniform.
    const finalManifest =
      manifest.cards.length > 0
        ? manifest
        : readStagedManifest(batchId, {
            story: v.story,
            projectPath: v.projectPath,
            mode: v.mode,
            deepPlanning: v.deepPlanning,
          });

    pending.set(batchId, {
      manifest: finalManifest,
      expiresAt: Date.now() + PENDING_TTL_MS,
    });
    // The planner is done writing; the triage inbox may now act on
    // this batch per-card.
    markStagingReady(batchId);

    write("dry_run", {
      batch_id: batchId,
      cards: finalManifest.cards,
      histogram: finalManifest.histogram,
      depends_on_edges: finalManifest.dependsOnEdges,
      claimable_count: finalManifest.claimableCount,
      mode: finalManifest.mode,
      deep_planning: finalManifest.deepPlanning,
    });
    res.end();
  } catch (err) {
    log.warn("invoker failed", { batchId, err: String(err) });
    try {
      cleanupStaging(batchId);
    } catch {
      /* swallow */
    }
    write("error", { message: String(err), stage: "planning" });
    res.end();
  }
}
