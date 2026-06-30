/**
 * REST routes for cards. Read-only listing + a single mutating endpoint
 * for the drag-drop move. Anything heavier (editing the body, deleting,
 * etc.) belongs in v1; the v0+ surface is intentionally narrow.
 */

import { Router, type Request, type Response } from "express";

import { appendRank } from "../db/ranks.js";
import {
  getCard,
  getColumns,
  listCards,
  moveCard,
  patchCardFrontmatter,
  STATUSES,
  type FrontmatterScalar,
  type StatusId,
} from "../fs/cards.js";

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

  router.patch("/cards/:id/frontmatter", (req: Request, res: Response) => {
    const id = req.params["id"];
    if (typeof id !== "string") {
      res.status(400).json({ error: "missing id" });
      return;
    }
    if (!getCard(id)) {
      res.status(404).json({ error: "no such card" });
      return;
    }
    const body = req.body as Record<string, unknown> | undefined;
    if (!body || typeof body !== "object") {
      res.status(400).json({ error: "body must be a JSON object" });
      return;
    }
    const validation = validateFrontmatterPatch(body);
    if (validation.kind === "error") {
      res.status(400).json({ error: validation.error });
      return;
    }
    try {
      const updated = patchCardFrontmatter(id, validation.patch);
      res.json({
        id: updated.id,
        file: updated.file,
        status: updated.status,
        frontmatter: updated.frontmatter,
        mtimeMs: updated.mtimeMs,
      });
    } catch (err) {
      res.status(500).json({ error: String(err) });
    }
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

/**
 * Whitelisted PATCH validator. The grid view and the backlog grooming
 * surface write back a tightly-scoped set of fields; anything else is
 * rejected so this route can't be turned into a generic frontmatter
 * editor (which would need a much broader threat model). Add a key to
 * the whitelist only after thinking about what disk-truth invariants it
 * touches.
 *
 *   - `stakes`: one of "low" | "medium" | "high" | null (null deletes).
 *   - `cost_cap_usd`: positive finite number, or null to clear.
 *   - `title`: non-empty string (trimmed). Grooming rename.
 *   - `points`: integer tier 1..6. Grooming re-size.
 *   - `ready`: boolean (the grooming "ready for sprint" label), or null
 *     to clear back to ice-box. Distinct from `status:`, which the
 *     runner owns; `ready` is a planner/operator grooming signal that
 *     never moves the card between folders.
 */
const ALLOWED_STAKES = new Set(["low", "medium", "high"]);
const MIN_TIER = 1;
const MAX_TIER = 6;

type PatchValidation =
  | { kind: "ok"; patch: Record<string, FrontmatterScalar> }
  | { kind: "error"; error: string };

function validateFrontmatterPatch(
  body: Record<string, unknown>
): PatchValidation {
  const out: Record<string, FrontmatterScalar> = {};
  for (const key of Object.keys(body)) {
    if (key === "stakes") {
      const v = body[key];
      if (v === null) {
        out["stakes"] = null;
        continue;
      }
      if (typeof v !== "string" || !ALLOWED_STAKES.has(v)) {
        return {
          kind: "error",
          error: `stakes must be one of low|medium|high|null, got ${JSON.stringify(v)}`,
        };
      }
      out["stakes"] = v;
      continue;
    }
    if (key === "cost_cap_usd") {
      const v = body[key];
      if (v === null) {
        out["cost_cap_usd"] = null;
        continue;
      }
      if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) {
        return {
          kind: "error",
          error: `cost_cap_usd must be a positive number or null, got ${JSON.stringify(v)}`,
        };
      }
      out["cost_cap_usd"] = v;
      continue;
    }
    if (key === "title") {
      const v = body[key];
      if (typeof v !== "string" || v.trim().length === 0) {
        return {
          kind: "error",
          error: `title must be a non-empty string, got ${JSON.stringify(v)}`,
        };
      }
      out["title"] = v.trim();
      continue;
    }
    if (key === "points") {
      const v = body[key];
      if (
        typeof v !== "number" ||
        !Number.isInteger(v) ||
        v < MIN_TIER ||
        v > MAX_TIER
      ) {
        return {
          kind: "error",
          error: `points must be an integer ${MIN_TIER}..${MAX_TIER}, got ${JSON.stringify(v)}`,
        };
      }
      out["points"] = v;
      continue;
    }
    if (key === "ready") {
      const v = body[key];
      if (v === null) {
        out["ready"] = null;
        continue;
      }
      if (typeof v !== "boolean") {
        return {
          kind: "error",
          error: `ready must be a boolean or null, got ${JSON.stringify(v)}`,
        };
      }
      out["ready"] = v;
      continue;
    }
    return { kind: "error", error: `field not patchable: ${key}` };
  }
  if (Object.keys(out).length === 0) {
    return { kind: "error", error: "empty patch" };
  }
  return { kind: "ok", patch: out };
}
