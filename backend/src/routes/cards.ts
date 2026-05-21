/**
 * REST routes for cards. Read-only listing + a single mutating endpoint
 * for the drag-drop move. Anything heavier (editing the body, deleting,
 * etc.) belongs in v1; the v0+ surface is intentionally narrow.
 */

import { Router, type Request, type Response } from "express";

import { appendRank } from "../db/ranks.js";
import { getCard, getColumns, listCards, moveCard, STATUSES, type StatusId } from "../fs/cards.js";

const VALID_STATUSES = new Set<string>(STATUSES.map((s) => s.id));

function isStatusId(v: unknown): v is StatusId {
  return typeof v === "string" && VALID_STATUSES.has(v);
}

export function cardsRouter(): Router {
  const router = Router();

  router.get("/columns", (_req: Request, res: Response) => {
    res.json({ columns: getColumns() });
  });

  router.get("/cards", (_req: Request, res: Response) => {
    const cards = listCards().map((c) => ({
      id: c.id,
      file: c.file,
      status: c.status,
      frontmatter: c.frontmatter,
      mtimeMs: c.mtimeMs,
    }));
    res.json({ cards });
  });

  router.get("/cards/:id", (req: Request, res: Response) => {
    const id = req.params["id"];
    if (typeof id !== "string") {
      res.status(400).json({ error: "missing id" });
      return;
    }
    const c = getCard(id);
    if (!c) {
      res.status(404).json({ error: "no such card" });
      return;
    }
    res.json({
      id: c.id,
      file: c.file,
      status: c.status,
      frontmatter: c.frontmatter,
      body: c.body,
      mtimeMs: c.mtimeMs,
    });
  });

  router.post("/cards/:id/move", (req: Request, res: Response) => {
    const id = req.params["id"];
    if (typeof id !== "string") {
      res.status(400).json({ error: "missing id" });
      return;
    }
    const body = req.body as { status?: unknown } | undefined;
    const status = body?.status;
    if (!isStatusId(status)) {
      res.status(400).json({
        error: "status must be one of",
        valid: Array.from(VALID_STATUSES),
      });
      return;
    }
    try {
      const moved = moveCard(id, status);
      // Cross-column moves drop the old rank and append at the bottom of
      // the new column. Same-column "moves" don't go through this route
      // (moveCard returns early on no-op), so we never overwrite an
      // existing rank with an append from this path.
      const newRank = appendRank(moved.id, moved.status);
      res.json({
        id: moved.id,
        file: moved.file,
        status: moved.status,
        rank: newRank,
      });
    } catch (err) {
      res.status(409).json({ error: String(err) });
    }
  });

  return router;
}
