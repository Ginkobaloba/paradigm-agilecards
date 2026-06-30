"""Handler: file_exists.

Pass if the path exists. Paths may be absolute or worktree-relative.
Symlinks count as existing as long as they resolve. A path that
exists but is unreadable still counts as existing; the readability
question is the handler-of-content's problem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult


def run(
    item: Mapping[str, Any],
    *,
    worktree: Path,
    project_cfg: ProjectConfig,
) -> HandlerResult:
    raw_path = item["path"]
    resolved = _resolve(raw_path, worktree=worktree)
    exists = resolved.exists()
    return HandlerResult(
        passed=exists,
        evidence={
            "declared_path": raw_path,
            "resolved_path": str(resolved),
            "exists": exists,
        },
    )


def _resolve(raw_path: str, *, worktree: Path) -> Path:
    """Resolve worktree-relative paths; leave absolute paths alone."""
    p = Path(raw_path)
    if p.is_absolute():
        return p
    return (worktree / p).resolve(strict=False)
