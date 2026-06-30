"""Handler: http_contains.

Make an HTTP request, assert the body matches a pattern or literal.
Shares the request plumbing with http_status; differs in the
pass condition (regex / substring match on body instead of status
match).

The status field, if declared, is treated as a precondition: if the
response status is outside `expected_status` (default any 2xx), the
match is not attempted and the check fails with the wrong-status
evidence. Retry policy mirrors http_status.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Mapping

from verifier.handlers.http_status import _RequestFailure, _do_request
from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult
from verifier.types import (
    DEFAULT_TIMEOUTS_SEC,
    HTTP_DEFAULT_BACKOFF_SEC,
    HTTP_DEFAULT_RETRIES,
    ACType,
)


def run(
    item: Mapping[str, Any],
    *,
    worktree: Path,
    project_cfg: ProjectConfig,
) -> HandlerResult:
    if not project_cfg.network_checks_allowed:
        return HandlerResult(
            passed=False,
            evidence={
                "url": item["url"],
                "error": (
                    "http_contains declared but project config does not set "
                    "network_checks_allowed: true."
                ),
            },
        )

    expected_status: frozenset[int] | None = None
    if "expected_status" in item:
        raw = item["expected_status"]
        expected_status = (
            frozenset({int(raw)})
            if isinstance(raw, int)
            else frozenset(int(v) for v in raw)
        )

    attempts = int(item.get("retries", HTTP_DEFAULT_RETRIES)) + 1
    case_sensitive = bool(item.get("case_sensitive", True))

    last_evidence: dict[str, Any] = {}
    for attempt in range(attempts):
        try:
            status, body = _do_request(item, project_cfg)
        except _RequestFailure as exc:
            last_evidence = {
                "url": item["url"],
                "attempt": attempt + 1,
                "error": str(exc),
            }
        else:
            if expected_status is not None and status not in expected_status:
                last_evidence = {
                    "url": item["url"],
                    "attempt": attempt + 1,
                    "status": status,
                    "expected_status": expected_status,
                    "body_excerpt": body,
                    "error": "status outside expected_status; body match not attempted",
                }
            else:
                matched, snippet = _match(
                    item, body, case_sensitive=case_sensitive
                )
                last_evidence = {
                    "url": item["url"],
                    "attempt": attempt + 1,
                    "status": status,
                    "matched": matched,
                    "body_excerpt": body[:512],
                }
                if matched:
                    if snippet is not None:
                        last_evidence["match_excerpt"] = snippet[:200]
                    return HandlerResult(passed=True, evidence=last_evidence)

        if attempt + 1 < attempts:
            sleep_idx = min(attempt, len(HTTP_DEFAULT_BACKOFF_SEC) - 1)
            time.sleep(HTTP_DEFAULT_BACKOFF_SEC[sleep_idx])

    return HandlerResult(passed=False, evidence=last_evidence)


def _match(
    item: Mapping[str, Any],
    body: str,
    *,
    case_sensitive: bool,
) -> tuple[bool, str | None]:
    if "pattern" in item:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(item["pattern"], flags=flags)
        except re.error as exc:
            return False, f"<invalid regex: {exc}>"
        m = compiled.search(body)
        return (m is not None, m.group(0) if m else None)
    literal: str = item["literal"]
    haystack = body if case_sensitive else body.lower()
    needle = literal if case_sensitive else literal.lower()
    idx = haystack.find(needle)
    return (idx >= 0, literal if idx >= 0 else None)
