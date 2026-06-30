"""Handler: file_absent.

Inverse of file_exists. Pass if the path does NOT exist. Useful for
"the migration script was deleted" or "the secrets file is no longer
shipped" style assertions.
"""
from __future__ import annotations

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
    raw_path = item["path"]
    resolved = _resolve(raw_path, worktree=worktree)
    exists = resolved.exists()
    return HandlerResult(
        passed=not exists,
        evidence={
            "declared_path": raw_path,
            "resolved_path": str(resolved),
            "exists": exists,
        },
    )
