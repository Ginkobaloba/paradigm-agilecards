"""Daemon subpackage.

The daemon owns polling, claim arbitration, worktree creation under
the global mutex, worker spawning, heartbeat detection, and orphan
reclaim. It holds no durable state; everything is filesystem-driven.

Chunk 1 ships a stub-executor worker, so the daemon's verifier
dispatch and merge orchestration are intentionally not present.
Those land in chunks 3 and 4.
"""
from __future__ import annotations
