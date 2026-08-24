"""In-process, org-keyed pub/sub for SSE (ADR D7).

CRUD routes run sync in FastAPI's threadpool; SSE connections are async
tasks on the main event loop. ``publish`` is therefore threadsafe: it hops
onto the loop captured at application startup. Publishing before startup (or
in tests without an SSE consumer) is a silent no-op by design.

Single-process only — multiple uvicorn workers would fragment the bus. The
scale-out path is Postgres LISTEN/NOTIFY behind this same interface.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

BoardEvent = dict[str, Any]


class OrgEventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: dict[str, set[asyncio.Queue[BoardEvent]]] = defaultdict(set)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, org_id: str) -> asyncio.Queue[BoardEvent]:
        queue: asyncio.Queue[BoardEvent] = asyncio.Queue()
        self._queues[org_id].add(queue)
        return queue

    def unsubscribe(self, org_id: str, queue: asyncio.Queue[BoardEvent]) -> None:
        self._queues[org_id].discard(queue)
        if not self._queues[org_id]:
            self._queues.pop(org_id, None)

    def publish(self, org_id: str, event: BoardEvent) -> None:
        """Threadsafe fan-out to this org's subscribers. Call AFTER commit —
        an event for a rolled-back mutation must never reach a client."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._fanout, org_id, event)

    def _fanout(self, org_id: str, event: BoardEvent) -> None:
        for queue in tuple(self._queues.get(org_id, ())):
            queue.put_nowait(event)
