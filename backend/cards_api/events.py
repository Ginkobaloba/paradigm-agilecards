"""In-process, org-scoped event bus for the SSE live channel (ADR-2026-07-16 §6).

Mutating routes publish board events; the ``/events`` SSE route subscribes.
Publishers run in FastAPI's threadpool (sync routes), subscribers on the event
loop, so hand-off goes through ``loop.call_soon_threadsafe``.

Correct for the single-process uvicorn deployment alpha uses. The interface is
deliberately small so a Postgres LISTEN/NOTIFY implementation can replace it
when the deployment goes multi-process -- documented in the ADR, not built.

Event names/payloads mirror the legacy wire contract exactly: the SSE ``event:``
field equals the payload's ``type`` field (card-added, card-updated,
card-removed, card-state-changed, card-event-added, heartbeat).
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from dataclasses import dataclass, field


@dataclass
class _Subscriber:
    org_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, _Subscriber] = {}
        self._ids = itertools.count(1)

    def subscribe(self, org_id: str) -> tuple[int, asyncio.Queue]:
        """Register the calling event-loop task for ``org_id``'s events."""
        sub = _Subscriber(org_id=org_id, loop=asyncio.get_running_loop())
        with self._lock:
            token = next(self._ids)
            self._subscribers[token] = sub
        return token, sub.queue

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, org_id: str, payload: dict) -> None:
        """Deliver ``payload`` to every subscriber of ``org_id``. Thread-safe;
        callable from sync routes running in the threadpool."""
        with self._lock:
            targets = [s for s in self._subscribers.values() if s.org_id == org_id]
        for sub in targets:
            sub.loop.call_soon_threadsafe(sub.queue.put_nowait, payload)
