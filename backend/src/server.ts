/**
 * Express entry point. Boots the SQLite database, starts the chokidar
 * watcher, mounts routes, listens.
 *
 * Everything is module-scoped state; we don't try to support multiple
 * instances inside one Node process. That keeps the code simple, and
 * the dashboard is deployed as a single process.
 */

import cors from "cors";
import express from "express";

import { config } from "./config.js";
import { log } from "./logger.js";
import { getDb } from "./db/sqlite.js";
import { startWatcher } from "./fs/cards.js";
import { getAuthContext, requireAuth } from "./routes/auth.js";
import { cardsRouter } from "./routes/cards.js";
import { ranksRouter } from "./routes/ranks.js";
import { ratesRouter } from "./routes/rates.js";
import { retrosRouter } from "./routes/retros.js";
import { sprintsRouter } from "./routes/sprints.js";
import { sseRouter } from "./routes/sse.js";
import { storiesRouter } from "./routes/stories.js";
import { viewsRouter } from "./routes/views.js";
import { demoInvoker } from "./stories/demoInvoker.js";

function main(): void {
  // Boot stateful things in order. SQLite first so any auth check before
  // the first request still works. Watcher second so the first
  // /api/cards call has a populated index.
  getDb();
  const watcher = startWatcher();

  const app = express();

  app.set("trust proxy", true);
  app.use(express.json({ limit: "256kb" }));
  app.use(
    cors({
      origin: (origin, cb) => {
        // Allow same-origin (no Origin) and any configured origin.
        // CORS_ORIGIN is a comma-separated list so the same backend can
        // serve standalone dev plus the Paradigm portal hostname.
        if (!origin || config.corsOrigins.includes(origin)) {
          return cb(null, true);
        }
        cb(new Error(`origin ${origin} not allowed`));
      },
      credentials: false,
    })
  );

  // Lightweight access log. Skipped at debug to avoid drowning the
  // watcher's debug output.
  app.use((req, res, next) => {
    const started = Date.now();
    res.on("finish", () => {
      log.info("req", {
        method: req.method,
        path: req.path,
        status: res.statusCode,
        ms: Date.now() - started,
        tokenLabel: getAuthContext(res)?.tokenLabel,
      });
    });
    next();
  });

  // Public health check, no auth. Useful for the Cloudflare tunnel and
  // for "is the container up" checks.
  app.get("/healthz", (_req, res) => {
    res.json({
      ok: true,
      cardsDir: config.cardsDir,
      version: "0.1.0",
    });
  });

  // Everything below is gated.
  app.use("/api", requireAuth);
  app.use("/api", cardsRouter());
  app.use("/api", ranksRouter());
  app.use("/api", ratesRouter());
  app.use("/api", sprintsRouter());
  app.use("/api", retrosRouter());
  app.use("/api", viewsRouter());
  // STORIES_DEMO_INVOKER swaps the real `claude` CLI planner for an
  // offline demo invoker, so the submit-story flow can be exercised
  // without a live runner. Unset in production -> real invoker.
  app.use(
    "/api",
    storiesRouter(
      process.env["STORIES_DEMO_INVOKER"] ? { invoker: demoInvoker } : {}
    )
  );

  // SSE has its own path so the frontend can target it directly. Auth
  // still required.
  app.use(requireAuth);
  app.use(sseRouter());

  const server = app.listen(config.port, () => {
    log.info("listening", { port: config.port });
  });

  const shutdown = (signal: string): void => {
    log.info("shutdown", { signal });
    void watcher.close();
    server.close(() => process.exit(0));
    // Hard-exit fallback in case Express keeps a connection alive.
    setTimeout(() => process.exit(1), 5_000).unref();
  };

  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
}

main();
