/**
 * Fetches `GET /api/rates` once after auth and caches the result in a
 * module-level promise so every consumer of the hook gets the same
 * payload without N round-trips.
 *
 * Returns a safe fallback (empty rate table, 0.6 ratio) while the fetch
 * is in flight. Empty rates -> cost helpers fall back to a sonnet-class
 * blend, so the tile shows *something* sane even before the table lands.
 */

import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { RatesPayload } from "../lib/cost";

const FALLBACK_PAYLOAD: RatesPayload = {
  rates: [],
  defaultInputRatio: 0.6,
};

let cached: RatesPayload | null = null;
let inFlight: Promise<RatesPayload> | null = null;

function loadRates(): Promise<RatesPayload> {
  if (cached) return Promise.resolve(cached);
  if (inFlight) return inFlight;
  inFlight = api
    .listRates()
    .then((p) => {
      cached = p;
      inFlight = null;
      return p;
    })
    .catch((err: unknown) => {
      // On failure, fall through to the fallback. Don't pollute the cache
      // so a later auth/network repair triggers another attempt.
      inFlight = null;
      throw err;
    });
  return inFlight;
}

export function useRates(isAuthed: boolean): RatesPayload {
  const [payload, setPayload] = useState<RatesPayload>(
    cached ?? FALLBACK_PAYLOAD
  );

  useEffect(() => {
    if (!isAuthed) return;
    if (cached) {
      setPayload(cached);
      return;
    }
    let cancelled = false;
    loadRates()
      .then((p) => {
        if (!cancelled) setPayload(p);
      })
      .catch(() => {
        // Already using the fallback; nothing to do. The chip will show a
        // sonnet-class blend, which is good enough for a first paint.
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthed]);

  return payload;
}
