"""Handler: file_absent_content.

Inverse of file_contains: pass if the file does NOT match the
pattern or literal. Use for "the hardcoded api key was removed" or
"the deprecated import is no longer referenced" assertions.

Implementation re-uses file_contains internals with the
`expect_match=False` flag flipped.

Special case: a missing file passes this check. Rationale: "the
hardcoded key is no longer in src/config.py" is satisfied just as
well by deleting src/config.py as by editing it. Card authors who
want "file exists AND lacks pattern" should declare two AC items
(one file_exists, one file_absent_content).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from verifier.handlers.file_contains import _run_match
from verifier.handlers.file_exists import _resolve
from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult


def run(
    item: Mapping[str, Any],
    *,
    worktree: Path,
    project_cfg: ProjectConfig,
) -> HandlerResult:
    resolved = _resolve(item["path"], worktree=worktree)
    if not resolved.exists():
        return HandlerResult(
            passed=True,
            evidence={
                "declared_path": item["path"],
                "resolved_path": str(resolved),
                "note": (
                    "file does not exist; treated as absent of content. "
                    "Add a file_exists AC item if presence is also required."
                ),
            },
        )
    return _run_match(item, worktree=worktree, expect_match=False)
