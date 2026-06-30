"""Verifier handler registry.

One handler per canonical acceptance-criterion type. Each handler is
pure Python (no network, no LLM) and returns a `HandlerResult`. The
subjective handler is the exception -- it makes a single batched LLM
call -- and lives in its own module because it speaks a different
shape (one call covers every subjective item at once).
"""
from __future__ import annotations

from typing import Callable

from .deterministic import (
    HandlerContext,
    HandlerResult,
    handle_file_absent,
    handle_file_contains,
    handle_file_exists,
    handle_file_lacks,
    handle_shell,
)


# Deterministic handlers, by canonical type name. The subjective type
# does not appear here; the runner batches subjective items and
# delegates to `verifier.handlers.subjective.evaluate_subjective_batch`.
DETERMINISTIC_HANDLERS: dict[
    str, Callable[[dict, HandlerContext], HandlerResult]
] = {
    "file_exists": handle_file_exists,
    "file_absent": handle_file_absent,
    "file_contains": handle_file_contains,
    "file_lacks": handle_file_lacks,
    "shell": handle_shell,
}


__all__ = [
    "DETERMINISTIC_HANDLERS",
    "HandlerContext",
    "HandlerResult",
]
