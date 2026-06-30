/**
 * In-process pub/sub for the SSE endpoint. The card watcher publishes; the
 * SSE route subscribes per client connection. Trivial event emitter
 * pattern; we could use Node's EventEmitter but a typed Set of callbacks
 * keeps the call sites obvious.
 */

export interface CardEventPayload {
  readonly id: number;
  readonly cardId: string;
  readonly type: string;
  readonly at: string;
  readonly details: unknown;
}

export type BoardEvent =
  | { type: "card-state-changed"; cardId: string; status: string }
  | { type: "card-added"; cardId: string; status: string }
  | { type: "card-removed"; cardId: string }
  | { type: "card-updated"; cardId: string; status: string }
  | { type: "card-event-added"; cardId: string; event: CardEventPayload }
  | { type: "sprint-status-changed"; sprintId: number }
  | { type: "heartbeat" };

type Listener = (event: BoardEvent) => void;

const listeners = new Set<Listener>();

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function publish(event: BoardEvent): void {
  for (const l of listeners) {
    try {
      l(event);
    } catch {
      // Drop the listener if it throws; better than letting one bad SSE
      // client take down the watcher.
      listeners.delete(l);
    }
  }
}

export function subscriberCount(): number {
  return listeners.size;
}
