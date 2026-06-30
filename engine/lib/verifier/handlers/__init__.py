"""Handler implementations for each canonical AC type.

Each module in this package exports a callable named `run` with the
signature:

    def run(
        item: Mapping[str, Any],
        *,
        worktree: pathlib.Path,
        project_cfg: ProjectConfig,
    ) -> HandlerResult

The handler is dispatched by `verifier.runner` based on the item's
`type` field. The handler is responsible for:

- Validating its own field values (the schema layer only checks shape).
- Producing a `HandlerResult` with `passed: bool` and a structured
  `evidence` dict that becomes part of `verifier_notes` on failure.
- Respecting `timeout_sec` if present; otherwise the type's default.

Handlers do NOT mutate the card. The runner owns persistence.
"""
from verifier.handlers import (
    command,
    file_absent,
    file_absent_content,
    file_contains,
    file_exists,
    http_contains,
    http_status,
    python_assert,
    subjective,
)

__all__ = [
    "command",
    "file_absent",
    "file_absent_content",
    "file_contains",
    "file_exists",
    "http_contains",
    "http_status",
    "python_assert",
    "subjective",
]
