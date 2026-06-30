/**
 * Saved-view CRUD. Every endpoint is scoped to the calling token; you
 * cannot enumerate, read, or edit a view belonging to a different
 * token. Sharing is done out-of-band via URL-encoded payloads (the
 * frontend writes those into the address bar).
 */

import { Router, type Request, type Response } from "express";

import {
  createView,
  deleteView,
  getView,
  listViews,
  updateView,
} from "../db/views.js";
import { getAuthContext } from "./auth.js";

const NAME_MAX = 80;
const PAYLOAD_MAX_BYTES = 16 * 1024;

function getTokenId(res: Response): number | null {
  const ctx = getAuthContext(res);
  return ctx?.tokenId ?? null;
}

function validName(v: unknown): v is string {
  return typeof v === "string" && v.trim().length > 0 && v.length <= NAME_MAX;
}

function validPayload(v: unknown): boolean {
  try {
    return JSON.stringify(v).length <= PAYLOAD_MAX_BYTES;
  } catch {
    return false;
  }
}

export function viewsRouter(): Router {
  const router = Router();

  router.get("/views", (_req: Request, res: Response) => {
    const tokenId = getTokenId(res);
    if (tokenId === null) {
      res.status(401).json({ error: "no auth context" });
      return;
    }
    res.json({ views: listViews(tokenId) });
  });

  router.post("/views", (req: Request, res: Response) => {
    const tokenId = getTokenId(res);
    if (tokenId === null) {
      res.status(401).json({ error: "no auth context" });
      return;
    }
    const body = req.body as
      | { name?: unknown; payload?: unknown }
      | undefined;
    if (!validName(body?.name)) {
      res
        .status(400)
        .json({ error: `name must be a non-empty string <= ${NAME_MAX} chars` });
      return;
    }
    if (!validPayload(body?.payload)) {
      res.status(400).json({ error: "payload too large or not JSON-serializable" });
      return;
    }
    try {
      const view = createView(tokenId, body.name.trim(), body.payload);
      res.status(201).json(view);
    } catch (err) {
      // Most likely: UNIQUE constraint violation on (token_id, name).
      res.status(409).json({ error: String(err) });
    }
  });

  router.patch("/views/:id", (req: Request, res: Response) => {
    const tokenId = getTokenId(res);
    if (tokenId === null) {
      res.status(401).json({ error: "no auth context" });
      return;
    }
    const id = Number.parseInt(req.params["id"] ?? "", 10);
    if (!Number.isFinite(id)) {
      res.status(400).json({ error: "missing or invalid id" });
      return;
    }
    const body = req.body as
      | { name?: unknown; payload?: unknown }
      | undefined;
    const patch: { name?: string; payload?: unknown } = {};
    if (body?.name !== undefined) {
      if (!validName(body.name)) {
        res.status(400).json({ error: "invalid name" });
        return;
      }
      patch.name = body.name.trim();
    }
    if (body?.payload !== undefined) {
      if (!validPayload(body.payload)) {
        res.status(400).json({ error: "payload too large" });
        return;
      }
      patch.payload = body.payload;
    }
    const updated = updateView(id, tokenId, patch);
    if (!updated) {
      res.status(404).json({ error: "no such view" });
      return;
    }
    res.json(updated);
  });

  router.delete("/views/:id", (req: Request, res: Response) => {
    const tokenId = getTokenId(res);
    if (tokenId === null) {
      res.status(401).json({ error: "no auth context" });
      return;
    }
    const id = Number.parseInt(req.params["id"] ?? "", 10);
    if (!Number.isFinite(id)) {
      res.status(400).json({ error: "missing or invalid id" });
      return;
    }
    const removed = deleteView(id, tokenId);
    if (!removed) {
      res.status(404).json({ error: "no such view" });
      return;
    }
    res.status(204).end();
  });

  // Convenience read-by-id, mostly for "share-by-id" links.
  router.get("/views/:id", (req: Request, res: Response) => {
    const tokenId = getTokenId(res);
    if (tokenId === null) {
      res.status(401).json({ error: "no auth context" });
      return;
    }
    const id = Number.parseInt(req.params["id"] ?? "", 10);
    if (!Number.isFinite(id)) {
      res.status(400).json({ error: "missing or invalid id" });
      return;
    }
    const view = getView(id, tokenId);
    if (!view) {
      res.status(404).json({ error: "no such view" });
      return;
    }
    res.json(view);
  });

  return router;
}
