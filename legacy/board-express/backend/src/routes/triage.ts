/**
 * Triage inbox routes (roadmap 2.4).
 *
 *   GET  /api/triage
 *     -> { batches: TriageBatch[] }  every staged batch on disk
 *
 *   POST /api/triage/:batchId/cards/:file/promote
 *     -> { id, status: "backlog", rank }  staged card -> backlog/
 *
 *   POST /api/triage/:batchId/cards/:file/decline
 *     -> { ok: true }  staged card -> _declined/<batchId>/
 *
 *   POST /api/triage/:batchId/cards/:file/merge  { targetId }
 *     -> { ok: true, targetId }  body absorbed into the target card,
 *        staged file declined (provenance kept)
 *
 * The `:file` segment is the staged file's basename, URL-encoded.
 * Validation in the triage module rejects anything that could escape
 * the staging tree.
 */

import { Router, type Request, type Response } from "express";

import { appendRank } from "../db/ranks.js";
import { appendToCardBody, getCard } from "../fs/cards.js";
import {
  TriageError,
  declineTriageCard,
  listTriage,
  promoteTriageCard,
  readStagedCardForMerge,
} from "../stories/triage.js";

export function triageRouter(): Router {
  const router = Router();

  router.get("/triage", (_req: Request, res: Response) => {
    res.json({ batches: listTriage() });
  });

  router.post(
    "/triage/:batchId/cards/:file/promote",
    (req: Request, res: Response) => {
      const params = requireParams(req, res);
      if (!params) return;
      try {
        const { id } = promoteTriageCard(params.batchId, params.file);
        const rank = appendRank(id, "backlog");
        res.json({ id, status: "backlog", rank });
      } catch (err) {
        sendTriageError(res, err);
      }
    }
  );

  router.post(
    "/triage/:batchId/cards/:file/decline",
    (req: Request, res: Response) => {
      const params = requireParams(req, res);
      if (!params) return;
      try {
        declineTriageCard(params.batchId, params.file);
        res.json({ ok: true });
      } catch (err) {
        sendTriageError(res, err);
      }
    }
  );

  router.post(
    "/triage/:batchId/cards/:file/merge",
    (req: Request, res: Response) => {
      const params = requireParams(req, res);
      if (!params) return;
      const body = req.body as { targetId?: unknown } | undefined;
      const targetId = body?.targetId;
      if (typeof targetId !== "string" || targetId.length === 0) {
        res.status(400).json({ error: "targetId is required" });
        return;
      }
      if (!getCard(targetId)) {
        res.status(404).json({ error: `no such card ${targetId}` });
        return;
      }
      try {
        const staged = readStagedCardForMerge(params.batchId, params.file);
        const marker = `## Absorbed from triage (${staged.id})`;
        const target = getCard(targetId);
        // Idempotent under retry: if a previous merge appended the
        // section but the decline step then failed (a 500 the user
        // retries), skip the append and just retire the staged file --
        // otherwise the target grows a duplicate section per retry.
        if (target && !target.raw.includes(marker)) {
          const section = [
            marker,
            "",
            `> ${staged.title} -- merged ${new Date().toISOString()}, batch ${params.batchId}`,
            "",
            staged.body,
          ].join("\n");
          appendToCardBody(targetId, section);
        }
        // Absorb succeeded (or already had); retire the staged file.
        // Decline (move to _declined) rather than delete so the merge
        // is reversible.
        declineTriageCard(params.batchId, params.file);
        res.json({ ok: true, targetId });
      } catch (err) {
        sendTriageError(res, err);
      }
    }
  );

  return router;
}

function requireParams(
  req: Request,
  res: Response
): { batchId: string; file: string } | null {
  const batchId = req.params["batchId"];
  const file = req.params["file"];
  if (typeof batchId !== "string" || batchId.length === 0) {
    res.status(400).json({ error: "missing batchId" });
    return null;
  }
  if (typeof file !== "string" || file.length === 0) {
    res.status(400).json({ error: "missing file" });
    return null;
  }
  return { batchId, file };
}

function sendTriageError(res: Response, err: unknown): void {
  if (err instanceof TriageError) {
    res.status(err.status).json({ error: err.message });
    return;
  }
  res.status(500).json({ error: String(err) });
}
