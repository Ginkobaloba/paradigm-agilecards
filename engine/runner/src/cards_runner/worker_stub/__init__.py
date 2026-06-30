"""Stub worker subprocess (chunk 1).

The stub worker does NOT call the Anthropic SDK. It exercises the
worker lifecycle - read the card, heartbeat, write completion notes,
exit cleanly - so chunk 1 can verify the daemon end-to-end at zero
token cost.

The `Invoker` seam lets chunk 2 swap in the real SDK-in-process
executor without touching the daemon. See `invoker.py` for the
abstraction.
"""
from __future__ import annotations
