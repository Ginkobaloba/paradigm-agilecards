"""Server-sent events: `GET /events?token=<jwt>` (EventSource cannot set
headers, so this one route accepts the JWT as a query parameter — same
tradeoff as legacy; the token can land in proxy logs, which is documented in
the deploy runbook). Wire format and 25 s heartbeat match the contract."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..audit import record_system_audit
from ..auth import TokenError
from ..bus import BoardEvent
from .common import err

router = APIRouter()

_bearer = HTTPBearer(auto_error=False)
HEARTBEAT_SECONDS = 25.0

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _frame(event: BoardEvent) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"


@router.get("/events")
async def events(
    request: Request,
    token: str | None = None,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> StreamingResponse:
    raw = creds.credentials if creds and creds.credentials else token
    if not raw:
        raise err(401, "missing_token")
    try:
        claims = request.app.state.verifier.verify(raw)
    except TokenError as exc:
        record_system_audit(
            request.app.state.session_factory,
            action="auth.sse_token_verify",
            outcome=str(exc),
        )
        raise err(401, str(exc)) from exc

    bus = request.app.state.bus
    queue = bus.subscribe(claims.org_id)

    async def stream():
        try:
            yield _frame({"type": "heartbeat"})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    event = {"type": "heartbeat"}
                yield _frame(event)
        finally:
            bus.unsubscribe(claims.org_id, queue)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
