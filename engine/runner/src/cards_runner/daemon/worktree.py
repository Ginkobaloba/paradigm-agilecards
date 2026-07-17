"""Worktree creation, verification, and teardown.

Per RUNNER_CONTRACT.md "Worktree isolation and cross-contamination
defense" the runner serializes `git worktree add` calls behind the
global `.runner.lock` mutex to defeat the `.git/config.lock` race
(Claude Code issue #34645).

Chunk 1 supports two modes:

- **Real mode** (default): we call `git worktree add` via subprocess
  and verify the four post-create checks. The card's `project` field
  names the source repo.
- **Skip mode** (`DaemonConfig.skip_worktree=True`): we create a plain
  directory under `_runs/<attempt>/worktree` and skip git entirely.
  Tests use this when running against a tmp directory that is not a
  git repo.

This module does not own the env scrub or the worker spawn; those
live in `daemon.spawner`. We only own the disk side of worktree
preparation.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

from ..common.locks import held_worktree_mutex
from ..common.types import RuntimePaths


log = logging.getLogger(__name__)

GIT_WORKTREE_TIMEOUT_SEC: Final[float] = 60.0


class WorktreeCreateError(Exception):
    """Raised when worktree creation or verification fails."""


def prepare_worktree(
    *,
    paths: RuntimePaths,
    project_dir: Path,
    branch_name: str,
    base_branch: str,
    worktree_path: Path,
    skip_git: bool = False,
) -> None:
    """Create and verify a per-card worktree.

    Holds the global mutex for the duration of the `git worktree add`
    call only. Verification runs outside the mutex.

    Steps:
        1. Acquire the global worktree-creation mutex.
        2. If skip_git: mkdir and return.
        3. Otherwise:
            a. Ensure the branch exists (create off base_branch if needed).
            b. `git worktree add <path> <branch>`.
        4. Release the mutex.
        5. Verify: directory exists and is non-empty; `git worktree
           list` includes the path; `git status` runs clean.

    Raises `WorktreeCreateError` on any failure. Caller is expected
    to roll back (move card back to backlog) and surface the error.
    """
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    with held_worktree_mutex(paths.runner_lock, timeout_sec=60.0):
        if skip_git:
            worktree_path.mkdir(parents=True, exist_ok=True)
            (worktree_path / ".cards-stub-worktree").write_text(
                f"branch={branch_name}\nbase={base_branch}\n",
                encoding="utf-8",
            )
            log.info("prepared stub worktree at %s", worktree_path)
            return

        if not (project_dir / ".git").exists():
            raise WorktreeCreateError(
                f"project_dir {project_dir} is not a git repo"
            )

        _ensure_branch(project_dir, branch_name, base_branch)

        try:
            _run_powershell_git(
                project_dir,
                ["worktree", "add", str(worktree_path), branch_name],
            )
        except subprocess.CalledProcessError as exc:
            raise WorktreeCreateError(
                f"git worktree add failed: rc={exc.returncode} "
                f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
            ) from exc

    if skip_git:
        return

    # Verification runs outside the mutex, but it must not be able to leave
    # a half-created worktree behind. `git worktree add` has already
    # succeeded and registered the worktree (and pinned `branch_name` to
    # it). If verification then raises, the caller rolls the card back to
    # `backlog` and the next poll re-claims it -- at which point
    # `git worktree add` fails with "branch already used by worktree" and
    # the card livelocks at poll cadence, forever, never reaching the
    # executor or the verifier.
    #
    # So `prepare_worktree` is atomic: either it returns with a verified
    # worktree, or it raises having removed the one it created.
    try:
        _verify_worktree(project_dir, worktree_path)
    except WorktreeCreateError:
        log.warning(
            "worktree verification failed for %s; removing the worktree this "
            "call created so the branch is not left pinned to it",
            worktree_path,
        )
        try:
            teardown_worktree(
                paths=paths,
                project_dir=project_dir,
                worktree_path=worktree_path,
                skip_git=False,
            )
        except Exception:  # noqa: BLE001 - teardown is best-effort
            log.exception(
                "worktree teardown after failed verification also failed for "
                "%s; `git worktree prune` may be required",
                worktree_path,
            )
        raise


def prune_git_worktrees(
    *,
    project_dir: Path,
    expire_after: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run `git worktree prune` against a project repo.

    Chunk 5 defensive sweep. The chunk-4 forensic reaper deletes the
    `_runs/<attempt>/` directory tree; that drops the worktree's
    filesystem but leaves the administrative `.git/worktrees/<id>/`
    entries that git tracks. `git worktree prune` is the official
    cleanup verb for those entries -- it removes any whose worktree
    directory has gone missing.

    Returns the `CompletedProcess` for the operator's log, or None when
    the project_dir is not a git repository (a skip-git daemon, or a
    test against a tmp dir). Failures are NOT raised: this is a
    defensive sweep, and a transient git error should not crash the
    tick.
    """
    if not (project_dir / ".git").exists():
        log.debug("prune skipped: %s is not a git repository", project_dir)
        return None
    args = ["worktree", "prune", "-v"]
    if expire_after:
        args.extend(["--expire", expire_after])
    try:
        return _run_powershell_git(project_dir, args)
    except subprocess.CalledProcessError as exc:
        log.warning(
            "git worktree prune failed in %s: rc=%s stderr=%r",
            project_dir, exc.returncode, exc.stderr,
        )
        return None
    except subprocess.TimeoutExpired as exc:
        log.warning("git worktree prune timed out in %s: %s", project_dir, exc)
        return None


def teardown_worktree(
    *,
    paths: RuntimePaths,
    project_dir: Path,
    worktree_path: Path,
    skip_git: bool = False,
) -> None:
    """Remove a worktree. Best-effort.

    Chunk 1 does NOT call this during normal lifecycle. Worktrees
    are preserved for the forensic TTL (default 24h) and reaped by
    the reaper (chunk 4). This function is exposed so the reaper
    has a single seam to invoke later.
    """
    with held_worktree_mutex(paths.runner_lock, timeout_sec=60.0):
        if skip_git:
            shutil.rmtree(worktree_path, ignore_errors=True)
            return
        try:
            _run_powershell_git(
                project_dir,
                ["worktree", "remove", "--force", str(worktree_path)],
            )
        except subprocess.CalledProcessError as exc:
            log.warning("git worktree remove failed: %s", exc)
            shutil.rmtree(worktree_path, ignore_errors=True)


def _ensure_branch(project_dir: Path, branch_name: str, base_branch: str) -> None:
    """Create `branch_name` off `base_branch` if it does not exist.

    A re-claim after orphan reclaim hits an existing branch; that is
    fine and we leave it alone.
    """
    try:
        _run_powershell_git(
            project_dir, ["rev-parse", "--verify", branch_name]
        )
        log.debug("branch %s already exists", branch_name)
        return
    except subprocess.CalledProcessError:
        pass
    _run_powershell_git(
        project_dir,
        ["branch", branch_name, base_branch],
    )


def _registered_worktrees(project_dir: Path) -> list[Path]:
    """Return the worktree paths git currently has registered for a repo.

    Parses `git worktree list --porcelain`, whose contract is one
    `worktree <path>` line per entry. We use the porcelain form on
    purpose: the human-readable form aligns columns and truncates, and
    is explicitly not a stable interface.

    Paths are resolved so callers can compare them to a `Path` without
    caring about separators, case, or relative segments.
    """
    listing = _run_powershell_git(project_dir, ["worktree", "list", "--porcelain"])
    found: list[Path] = []
    for line in listing.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        raw = line[len("worktree "):].strip()
        if not raw:
            continue
        try:
            found.append(Path(raw).resolve())
        except (OSError, ValueError):  # pragma: no cover - defensive
            log.debug("unparseable worktree path from git: %r", raw)
    return found


def _verify_worktree(project_dir: Path, worktree_path: Path) -> None:
    """Run the four post-create checks. Raises `WorktreeCreateError` on miss."""
    if not worktree_path.exists():
        raise WorktreeCreateError(f"{worktree_path} does not exist after create")
    if not any(worktree_path.iterdir()):
        raise WorktreeCreateError(f"{worktree_path} is empty after create")

    # Compare resolved Paths, NOT substrings of the listing.
    #
    # This check used to be `str(worktree_path) not in listing.stdout`, which
    # could never pass on Windows: `str(Path(...))` yields backslashes
    # (`C:\dev\...`) while git always reports worktree paths POSIX-style
    # (`C:/dev/...`). Every worktree prep therefore failed its own
    # post-condition on the development platform, rolled back, and (see
    # `prepare_worktree`) livelocked the card. Path comparison is a
    # path-level question; doing it on raw strings was the bug.
    registered = _registered_worktrees(project_dir)
    if worktree_path.resolve() not in registered:
        raise WorktreeCreateError(
            f"{worktree_path} not present in `git worktree list` "
            f"(registered: {[str(p) for p in registered]})"
        )

    try:
        _run_powershell_git(worktree_path, ["status"])
    except subprocess.CalledProcessError as exc:
        raise WorktreeCreateError(
            f"`git status` failed in {worktree_path}: {exc}"
        ) from exc

    # Defensive: no leftover `.git/*.lock` files.
    git_dir = worktree_path / ".git"
    if git_dir.is_dir():
        for entry in git_dir.iterdir():
            if entry.name.endswith(".lock"):
                raise WorktreeCreateError(
                    f"leftover lock file {entry} in worktree"
                )


def _run_powershell_git(
    cwd: Path,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    """Run `git <args>` in `cwd`.

    Per SESSION_PROTOCOL.md section 7, git ops on Windows go through
    PowerShell. On non-Windows hosts we fall back to direct invocation
    so the suite runs on Linux CI. The chunk 1 tests use skip-git
    mode, so this path is exercised mainly on Windows during real
    operation.
    """
    if sys.platform == "win32":
        # Invoke PowerShell with the git command. Use `& git ...` so
        # PowerShell does not parse positional arguments oddly.
        ps_args = ["git", *args]
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "& " + " ".join(_quote_for_powershell(a) for a in ps_args),
        ]
    else:
        cmd = ["git", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_WORKTREE_TIMEOUT_SEC,
    )
    return proc


def _quote_for_powershell(arg: str) -> str:
    """Quote an argument for the PowerShell -Command string.

    Embedded single quotes are doubled; the whole thing is wrapped
    in single quotes. Good enough for git arguments (which do not
    contain single quotes in practice).
    """
    return "'" + arg.replace("'", "''") + "'"
