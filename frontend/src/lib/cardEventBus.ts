/**
 * Tiny typed pub/sub for live `card-event-added` SSE events. The useSSE
 * hook publishes; the Timeline component subscribes for its open cardId.
 *
 * Kept out of the Zustand store on purpose: timeline events can be large
 * and are only interesting while a card detail is open. Scoping them to
 * a subscribe-on-mount / unsubscribe-on-unmount lifecycle avoids paying
 * the memory cost for a feature that's invisible 90% of the time.
 */

import type { CardEventRow } from "./api";

type Listener = (event: CardEventRow) => void;

const listeners = new Set<Listener>();

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function publish(event: CardEventRow): void {
  for (const l of listeners) {
    try {
      l(event);
    } catch {
      // A throwing listener should not break the rest of the fan-out.
      listeners.delete(l);
    }
  }
}
