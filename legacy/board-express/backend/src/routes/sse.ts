/**
 * Server-Sent Events. One connection per browser tab. The watcher in
 * fs/cards.ts publishes BoardEvents on the in-process bus, and this route
 * fans them out to every connected client.
 *
 * We send a heartbeat comment every 25 seconds so any proxy with an idle
 * timeout below 30s keeps the connection alive. Cloudflare's default
 * tunnel idle timeout is 100s, so 25s is conservative.
 */

import { Router, type Request, type Response } from "express";

import { subscribe, type BoardEvent } from "../events/bus.js";
import { log } from "../logger.js";

const HEARTBEAT_MS = 25_000;

export function sseRouter(): Router {
  const router = Router();

  router.get("/events", (req: Request, res: Response) => {
    res.status(200);
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no"); // for nginx
    res.flushHeaders();

    const write = (evt: BoardEvent): void => {
      try {
        res.write(`event: ${evt.type}\n`);
        res.write(`data: ${JSON.stringify(evt)}\n\n`);
      } catch (err) {
        log.warn("sse write failed", { err: String(err) });
      }
    };

    // Send an immediate hello so the client knows we're alive.
    write({ type: "heartbeat" });

    const unsubscribe = subscribe(write);

    const beat = setInterval(() => write({ type: "heartbeat" }), HEARTBEAT_MS);

    const close = (): void => {
      clearInterval(beat);
      unsubscribe();
      try {
        res.end();
      } catch {
        /* swallow */
      }
    };

    req.on("close", close);
    req.on("aborted", close);
  });

  return router;
}
