"""SSE live channel (legacy ``GET /events``).

Wire format per legacy ``routes/sse.ts``: each event is
``event: <type>\\ndata: <json>\\n\\n`` where the JSON payload also carries the
``type`` field. An immediate heartbeat on connect, then one every 25 s.

Auth accepts ``?token=`` here and only here: EventSource cannot set headers.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..auth import ParadigmClaims
from ..deps import get_bus, require_claims_header_or_query
from ..events import EventBus

router = APIRouter()

HEARTBEAT_SECONDS = 25.0

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _frame(payload: dict) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n"


@router.get("/events")
async def events(
    request: Request,
    claims: ParadigmClaims = Depends(require_claims_header_or_query),
    bus: EventBus = Depends(get_bus),
) -> StreamingResponse:
    async def stream():
        token, queue = bus.subscribe(claims.org_id)
        try:
            yield _frame({"type": "heartbeat"})
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                    yield _frame(payload)
                except TimeoutError:
                    yield _frame({"type": "heartbeat"})
        finally:
            bus.unsubscribe(token)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)
