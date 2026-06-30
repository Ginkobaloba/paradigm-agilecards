"""Handler: file_contains.

Pass if the file at `path` contains at least one match of either
`pattern` (regex, Python `re` flavor) or `literal` (exact substring).
Exactly one of `pattern` or `literal` is required; the schema layer
enforces the xor.

Files are read as UTF-8 with `errors="replace"` so a non-UTF-8 byte
doesn't blow up the verifier on a successful card. Replacement
characters do show up in evidence if the regex happens to match
them, which is usually visible enough for a human to notice.

Missing files fail the check (with an explanatory evidence dict)
rather than raising, so a single failing card doesn't take down the
whole verifier pass.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from verifier.handlers.file_exists import _resolve
from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult


def run(
    item: Mapping[str, Any],
    *,
    worktree: Path,
    project_cfg: ProjectConfig,
) -> HandlerResult:
    return _run_match(item, worktree=worktree, expect_match=True)


def _run_match(
    item: Mapping[str, Any],
    *,
    worktree: Path,
    expect_match: bool,
) -> HandlerResult:
    raw_path = item["path"]
    resolved = _resolve(raw_path, worktree=worktree)
    case_sensitive = bool(item.get("case_sensitive", True))

    if not resolved.exists():
        return HandlerResult(
            passed=False,
            evidence={
                "declared_path": raw_path,
                "resolved_path": str(resolved),
                "error": "file does not exist; cannot evaluate content match",
            },
        )

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return HandlerResult(
            passed=False,
            evidence={
                "declared_path": raw_path,
                "resolved_path": str(resolved),
                "error": f"OSError reading file: {exc}",
            },
        )

    matched, matched_text = _evaluate(item, content, case_sensitive=case_sensitive)
    passed = matched if expect_match else not matched

    evidence: dict[str, Any] = {
        "declared_path": raw_path,
        "resolved_path": str(resolved),
        "matched": matched,
        "case_sensitive": case_sensitive,
    }
    if matched and matched_text is not None:
        evidence["match_excerpt"] = matched_text[:200]

    return HandlerResult(passed=passed, evidence=evidence)


def _evaluate(
    item: Mapping[str, Any],
    content: str,
    *,
    case_sensitive: bool,
) -> tuple[bool, str | None]:
    """Return (matched, first_match_text_or_None)."""
    if "pattern" in item:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(item["pattern"], flags=flags)
        except re.error as exc:
            # Invalid regex is a schema problem in spirit, but we
            # surface it as a content failure so the runner can keep
            # going across other items.
            return False, f"<invalid regex: {exc}>"
        m = compiled.search(content)
        return (m is not None, m.group(0) if m else None)

    literal: str = item["literal"]
    haystack = content if case_sensitive else content.lower()
    needle = literal if case_sensitive else literal.lower()
    idx = haystack.find(needle)
    return (idx >= 0, literal if idx >= 0 else None)
