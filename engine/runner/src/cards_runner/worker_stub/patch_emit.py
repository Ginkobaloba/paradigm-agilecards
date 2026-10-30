"""Hand the worker's change back to the daemon as a patch file.

The daemon no longer reads the worker's clone with git (daemon/clones.py):
it reads this file as data and applies it in a clone of its own. So the
worker diffs its clone against the base sha the daemon pinned, staged
and committed work alike, and writes the result into the run dir.

This git runs in a clone model-run code wrote, inside the worker
process. It gets the hardened env (git_safety): no API key or other
scrubbed variable, no global or system config, an empty hooks dir, no
fsmonitor, no external diff or textconv. Whatever the clone's config
still runs (a clean filter, say) therefore runs with no more than the
model already had. The patch content is untrusted either way; the daemon
checks it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

from ..common.git_safety import hardened_git_env, resolved
from ..common.types import PATCH_DONE_NAME, PATCH_NAME, WORKER_SCRATCH_FILES

log = logging.getLogger(__name__)

_GIT_TIMEOUT_SEC = 300.0


def _git(worktree: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    env = {**env, "GIT_CEILING_DIRECTORIES": str(worktree.resolve().parent)}
    return subprocess.run([resolved("git"), *args], cwd=str(worktree), env=env, capture_output=True,
                          timeout=_GIT_TIMEOUT_SEC, check=False)


def emit_patch(*, worktree: Path, base_sha: str, run_dir: Path) -> bool:
    """Write `<run_dir>/result.patch` (then its done marker). True on success."""
    env = hardened_git_env(keep_gh_credentials=False)
    excludes = [f":(exclude,top){name}" for name in WORKER_SCRATCH_FILES]
    staged = _git(worktree, ["add", "-A", "--", ".", *excludes], env)
    if staged.returncode != 0:
        log.error("git add failed (rc=%d): %s", staged.returncode,
                  staged.stderr.decode("utf-8", "replace")[:400])
        return False
    diff = _git(worktree, [
        "diff", "--cached", "--binary", "--full-index", "--no-renames",
        "--no-ext-diff", "--no-textconv", "--no-color",
        "--src-prefix=a/", "--dst-prefix=b/", base_sha, "--", ".", *excludes,
    ], env)
    if diff.returncode != 0:
        log.error("git diff failed (rc=%d): %s", diff.returncode,
                  diff.stderr.decode("utf-8", "replace")[:400])
        return False
    tmp = run_dir / (PATCH_NAME + ".tmp")
    tmp.write_bytes(diff.stdout)
    os.replace(tmp, run_dir / PATCH_NAME)
    # The marker is how the daemon tells a finished patch from one cut off
    # by a crash or a kill.
    (run_dir / PATCH_DONE_NAME).write_text(
        f"sha256={hashlib.sha256(diff.stdout).hexdigest()}\nbytes={len(diff.stdout)}\n",
        encoding="utf-8",
    )
    log.info("wrote patch: %d bytes against %s", len(diff.stdout), base_sha[:12])
    return True


__all__ = ["emit_patch"]
