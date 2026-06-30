/**
 * Per-model rate table endpoint. Frontend reads this once at boot and
 * caches it; cost chips on tiles use the cached table to compute USD
 * without round-tripping per card.
 *
 * Kept deliberately tiny — no per-token computation here, that's a
 * frontend concern once the table is on hand. If we ever need to recompute
 * historical cost server-side (e.g. for a retro export), this is the
 * module to import from.
 */

import { Router, type Request, type Response } from "express";

import { DEFAULT_INPUT_RATIO, MODEL_RATES } from "../cost/rates.js";

export function ratesRouter(): Router {
  const router = Router();

  router.get("/rates", (_req: Request, res: Response) => {
    res.json({
      rates: MODEL_RATES,
      defaultInputRatio: DEFAULT_INPUT_RATIO,
    });
  });

  return router;
}
