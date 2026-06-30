/**
 * Retro routes. Stubbed for v0+; schema is live in SQLite.
 *
 *   GET  /api/retros      -> list
 *   POST /api/retros      -> create
 *   GET  /api/retros/:id  -> detail
 */

import { Router, type Request, type Response } from "express";

import { getDb } from "../db/sqlite.js";

interface RetroRow {
  id: number;
  sprint_id: number | null;
  held_on: string;
  summary: string | null;
  created_at: string;
}

export function retrosRouter(): Router {
  const router = Router();

  router.get("/retros", (_req: Request, res: Response) => {
    const rows = getDb()
      .prepare(
        `SELECT id, sprint_id, held_on, summary, created_at FROM retros ORDER BY held_on DESC`
      )
      .all() as RetroRow[];
    res.json({ retros: rows });
  });

  router.post("/retros", (req: Request, res: Response) => {
    const body = req.body as
      | { sprintId?: unknown; heldOn?: unknown; summary?: unknown }
      | undefined;
    const sprintId =
      typeof body?.sprintId === "number" ? body.sprintId : null;
    const heldOn = typeof body?.heldOn === "string" ? body.heldOn : "";
    const summary = typeof body?.summary === "string" ? body.summary : null;
    if (!heldOn) {
      res.status(400).json({ error: "heldOn required (ISO date)" });
      return;
    }
    const info = getDb()
      .prepare<[number | null, string, string | null]>(
        `INSERT INTO retros (sprint_id, held_on, summary) VALUES (?, ?, ?)`
      )
      .run(sprintId, heldOn, summary);
    res.status(201).json({ id: Number(info.lastInsertRowid) });
  });

  router.get("/retros/:id", (req: Request, res: Response) => {
    const id = Number.parseInt(req.params["id"] ?? "", 10);
    if (!Number.isFinite(id)) {
      res.status(400).json({ error: "bad id" });
      return;
    }
    const r = getDb()
      .prepare<[number]>(
        `SELECT id, sprint_id, held_on, summary, created_at FROM retros WHERE id = ?`
      )
      .get(id) as RetroRow | undefined;
    if (!r) {
      res.status(404).json({ error: "no such retro" });
      return;
    }
    res.json(r);
  });

  return router;
}
