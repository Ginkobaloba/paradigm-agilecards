"""Handler: http_status.

Make an HTTP request, assert the response status. Gated behind
`project_cfg.network_checks_allowed`; the schema validator already
refuses the item if the gate is off, but the runtime check is
duplicated as defense in depth (a hand-edited card that wasn't run
through the planner could otherwise sneak past).

Retry policy: locked at 2 retries with exponential backoff 1s, 2s.
Per-item `retries` overrides the count; the backoff schedule is
fixed to keep the contract narrow. Retries cover network errors AND
"status not in expected_status"; the latter is the more common
flake mode in CI environments.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

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
                    "http_status declared but project config does not set "
                    "network_checks_allowed: true. Opt in at the project "
                    "level if these checks are intended."
                ),
            },
        )

    expected = _normalize_expected(item["expected_status"])
    attempts = int(item.get("retries", HTTP_DEFAULT_RETRIES)) + 1

    last_evidence: dict[str, Any] = {}
    for attempt in range(attempts):
        try:
            status, body_snippet = _do_request(item, project_cfg)
        except _RequestFailure as exc:
            last_evidence = {
                "url": item["url"],
                "attempt": attempt + 1,
                "error": str(exc),
            }
        else:
            last_evidence = {
                "url": item["url"],
                "attempt": attempt + 1,
                "status": status,
                "expected_status": expected,
                "body_excerpt": body_snippet,
            }
            if status in expected:
                return HandlerResult(passed=True, evidence=last_evidence)

        # Backoff before the next attempt, if any remain.
        if attempt + 1 < attempts:
            sleep_idx = min(attempt, len(HTTP_DEFAULT_BACKOFF_SEC) - 1)
            time.sleep(HTTP_DEFAULT_BACKOFF_SEC[sleep_idx])

    return HandlerResult(passed=False, evidence=last_evidence)


class _RequestFailure(Exception):
    """Internal: any error before we got a structured HTTP response."""


def _do_request(
    item: Mapping[str, Any],
    project_cfg: ProjectConfig,
) -> tuple[int, str]:
    """Issue one HTTP request and return (status, body_excerpt).

    Uses `requests` if it's importable (the common path); falls back
    to the stdlib `urllib.request` so the handler does not hard
    require a third-party dependency.
    """
    method = str(item.get("method", "GET")).upper()
    headers = dict(item.get("headers") or {})
    body = item.get("body")
    url = item["url"]
    timeout = _resolve_timeout(item, project_cfg)

    try:
        import requests  # type: ignore[import-not-found]
    except ImportError:
        return _do_request_urllib(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
        )

    kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": headers,
        "timeout": timeout,
        "allow_redirects": True,
    }
    if body is not None:
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["data"] = body

    try:
        resp = requests.request(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise _RequestFailure(
            f"{type(exc).__name__} contacting {url}: {exc}"
        ) from exc

    return resp.status_code, (resp.text or "")[:512]


def _do_request_urllib(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Any,
    timeout: float,
) -> tuple[int, str]:
    import json as _json
    import urllib.error
    import urllib.request

    if body is None:
        data: bytes | None = None
    elif isinstance(body, (dict, list)):
        data = _json.dumps(body).encode("utf-8")
        headers = {**dict(headers), "Content-Type": "application/json"}
    elif isinstance(body, str):
        data = body.encode("utf-8")
    elif isinstance(body, (bytes, bytearray)):
        data = bytes(body)
    else:
        raise _RequestFailure(
            f"unsupported body type for HTTP request: {type(body).__name__}"
        )

    req = urllib.request.Request(
        url=url, data=data, method=method, headers=dict(headers)
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        content = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
    except Exception as exc:  # noqa: BLE001
        raise _RequestFailure(
            f"{type(exc).__name__} contacting {url}: {exc}"
        ) from exc

    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = repr(content[:512])
    return status, text[:512]


def _normalize_expected(value: Any) -> frozenset[int]:
    if isinstance(value, int):
        return frozenset({value})
    return frozenset(int(v) for v in value)


def _resolve_timeout(
    item: Mapping[str, Any],
    project_cfg: ProjectConfig,
) -> int:
    if "timeout_sec" in item:
        return int(item["timeout_sec"])
    project_override = project_cfg.type_timeout_overrides_sec.get(
        ACType.HTTP_STATUS.value
    )
    if project_override is not None:
        return int(project_override)
    default = DEFAULT_TIMEOUTS_SEC[ACType.HTTP_STATUS.value]
    assert default is not None
    return default
