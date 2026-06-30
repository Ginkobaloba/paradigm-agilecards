/**
 * Routes for the manual-rank surface.
 *
 *   GET  /api/ranks               -> all current ranks
 *   POST /api/cards/:id/rank      -> { status, prevId?, nextId? } -> new rank
 *
 * The card itself must already exist (we don't validate against the
 * filesystem here -- if a rank gets set for a stale id, it sits unused
 * until that id reappears or gets garbage-collected). The frontend
 * passes neighbor card ids; we look up their persisted ranks
 * server-side, so two clients dropping the same card between the same
 * neighbors will land at the same numeric rank.
 */

import { Router, type Request, type Response } from "express";

import { STATUSES, type StatusId } from "../fs/cards.js";
import {
  getAllRanks,
  setRankBetween,
} from "../db/ranks.js";

const VALID_STATUSES = new Set<string>(STATUSES.map((s) => s.id));

function isStatusId(v: unknown): v is StatusId {
  return typeof v === "string" && VALID_STATUSES.has(v);
}

export function ranksRouter(): Router {
  const router = Router();

  router.get("/ranks", (_req: Request, res: Response) => {
    res.json({ ranks: getAllRanks() });
  });

  router.post("/cards/:id/rank", (req: Request, res: Response) => {
    const id = req.params["id"];
    if (typeof id !== "string" || id.length === 0) {
      res.status(400).json({ error: "missing id" });
      return;
    }
    const body = req.body as
      | { status?: unknown; prevId?: unknown; nextId?: unknown }
      | undefined;
    const status = body?.status;
    if (!isStatusId(status)) {
      res.status(400).json({
        error: "status must be one of",
        valid: Array.from(VALID_STATUSES),
      });
      return;
    }
    const prevId =
      typeof body?.prevId === "string" && body.prevId.length > 0
        ? body.prevId
        : null;
    const nextId =
      typeof body?.nextId === "string" && body.nextId.length > 0
        ? body.nextId
        : null;

    const rank = setRankBetween(id, status, prevId, nextId);
    res.json({ cardId: id, status, rank });
  });

  return router;
}
