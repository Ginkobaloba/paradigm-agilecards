/**
 * Per-card lifecycle event history.
 *
 *   GET /api/cards/:id/events?limit=&since=
 *     -> { events: CardEventRow[] }
 *
 * The frontend timeline pulls a card's history on modal open, then
 * patches it forward from the `card-event-added` SSE events.
 */

import { Router, type Request, type Response } from "express";

import { getEventsForCard } from "../db/events.js";

export function eventsRouter(): Router {
  const router = Router();

  router.get("/cards/:id/events", (req: Request, res: Response) => {
    const id = req.params["id"];
    if (typeof id !== "string" || id.length === 0) {
      res.status(400).json({ error: "missing id" });
      return;
    }

    const limitParam = req.query["limit"];
    const sinceParam = req.query["since"];

    const limit =
      typeof limitParam === "string"
        ? Math.max(1, Math.min(parseInt(limitParam, 10) || 500, 1000))
        : 500;
    const since = typeof sinceParam === "string" ? sinceParam : undefined;

    const events = getEventsForCard(id, { limit, since });
    res.json({ events });
  });

  return router;
}
