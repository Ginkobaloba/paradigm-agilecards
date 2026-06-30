/**
 * SSE connection. EventSource can't send headers, so we pass the token
 * via a query param. The backend accepts that on /events specifically.
 *
 * On each event the hook patches the Zustand store. Card removals and
 * status changes route to the right reducer; the watcher's `card-added`
 * / `card-updated` events trigger a single-card refetch so we get the
 * latest frontmatter without holding a separate REST cache in here.
 */

import { useEffect } from "react";

import { api, type CardEventRow } from "../lib/api";
import { getToken } from "../lib/auth";
import { apiPath } from "../lib/baseUrl";
import { publish as publishCardEvent } from "../lib/cardEventBus";
import { useStore } from "../state/store";

interface EventEnvelope {
  type: string;
  cardId?: string;
  status?: string;
  event?: CardEventRow;
}

export function useSSE(authed: boolean) {
  const upsert = useStore((s) => s.upsert);
  const remove = useStore((s) => s.remove);

  useEffect(() => {
    if (!authed) return;
    const token = getToken();
    if (!token) return;

    const url = apiPath(`/events?token=${encodeURIComponent(token)}`);
    const es = new EventSource(url);

    const handleCardChange = (cardId: string): void => {
      // Refetch the canonical card from the API. Cheaper than wiring the
      // full frontmatter through SSE, and keeps the store schema honest.
      void api
        .getCard(cardId)
        .then((c) => upsert(c))
        .catch(() => {
          // Card vanished between event and refetch. The matching
          // card-removed event will arrive separately.
        });
    };

    const onMessage = (raw: string): void => {
      let evt: EventEnvelope;
      try {
        evt = JSON.parse(raw) as EventEnvelope;
      } catch {
        return;
      }
      if (!evt || typeof evt.type !== "string") return;
      switch (evt.type) {
        case "card-added":
        case "card-updated":
        case "card-state-changed":
          if (typeof evt.cardId === "string") handleCardChange(evt.cardId);
          break;
        case "card-removed":
          if (typeof evt.cardId === "string") remove(evt.cardId);
          break;
        case "card-event-added":
          if (evt.event) publishCardEvent(evt.event);
          break;
        case "heartbeat":
        default:
          break;
      }
    };

    es.addEventListener("message", (ev: MessageEvent<string>) => onMessage(ev.data));
    es.addEventListener("card-added", (ev: MessageEvent<string>) => onMessage(ev.data));
    es.addEventListener("card-updated", (ev: MessageEvent<string>) => onMessage(ev.data));
    es.addEventListener("card-removed", (ev: MessageEvent<string>) => onMessage(ev.data));
    es.addEventListener("card-state-changed", (ev: MessageEvent<string>) =>
      onMessage(ev.data)
    );
    es.addEventListener("card-event-added", (ev: MessageEvent<string>) =>
      onMessage(ev.data)
    );
    es.addEventListener("heartbeat", () => {
      /* keepalive */
    });
    es.addEventListener("error", () => {
      // EventSource auto-reconnects; nothing to do here. We could surface
      // a banner if disconnects get long, but skip that for v0+.
    });

    return () => {
      es.close();
    };
  }, [authed, upsert, remove]);
}
