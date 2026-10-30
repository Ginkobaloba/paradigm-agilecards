"""The per-attempt branch name, and the stub tree for skip-worktree mode.

Real cards no longer get a `git worktree`: a worktree shares the
project's `.git` with the worker, so the daemon's next git call in the
project read whatever model-run code had written there. Each worker now
gets a throwaway clone (daemon/clones.py). What is left here:

- `attempt_branch_name`, shared by clone prep and the merge gate.
- `prepare_stub_worktree`: a plain directory for `skip_worktree` mode,
  where tests run the daemon against a tmp dir that is not a repo.
"""
from __future__ import annotations

import logging
from pathlib import Path


log = logging.getLogger(__name__)


def attempt_branch_name(base_branch: str, attempt_trace_id: str) -> str:
    """A per-attempt working branch name.

    A card's working branch cannot be a fixed name. After a verify-fail
    bounce the card returns to `backlog` and is re-claimed while the prior
    attempt's branch may still exist (pushed, or awaiting review). A fixed
    name therefore collides on the next attempt's push or PR, and the
    claim-fail-bounce-retry loop the runner is built around cannot retry.

    Suffixing the attempt id makes each claim's branch unique, so a retry
    proceeds instead of colliding, and each attempt's clone and PR are
    independently traceable. `base_branch` is used only as a prefix and is
    never created as a bare ref, so no `card/<id>` vs `card/<id>/<attempt>`
    directory/file ref conflict can arise.

    Callers that create OR reference the branch for a given attempt (clone
    prep, and the merge gate's push + PR) must derive it here, so the name
    is identical across the attempt's whole lifecycle.
    """
    return f"{base_branch}/{attempt_trace_id[:12]}"


def prepare_stub_worktree(*, worktree_path: Path, branch_name: str, base_branch: str) -> None:
    """Skip-worktree mode: a plain directory, no git."""
    worktree_path.mkdir(parents=True, exist_ok=True)
    (worktree_path / ".cards-stub-worktree").write_text(
        f"branch={branch_name}\nbase={base_branch}\n",
        encoding="utf-8",
    )
    log.info("prepared stub worktree at %s", worktree_path)
