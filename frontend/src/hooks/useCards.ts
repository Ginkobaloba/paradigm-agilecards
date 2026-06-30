/**
 * Hydrates the card store from the REST endpoint. Components that just
 * want the list re-render via Zustand selectors; this hook only handles
 * the loading lifecycle.
 */

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import { useStore } from "../state/store";

export function useCards(authed: boolean) {
  const setAll = useStore((s) => s.setAll);
  const setAllRanks = useStore((s) => s.setAllRanks);
  const hydrated = useStore((s) => s.hydrated);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    if (!authed) return;
    setLoading(true);
    setError(null);
    try {
      // Cards and ranks are independent endpoints; fetch them in
      // parallel so the first paint waits on the slower of the two.
      const [cardsRes, ranksRes] = await Promise.all([
        api.listCards(),
        api.listRanks().catch(() => ({ ranks: [] })),
      ]);
      setAll(cardsRes.cards);
      setAllRanks(ranksRes.ranks);
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
      else setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [authed, setAll, setAllRanks]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { loading, error, hydrated, refresh };
}
