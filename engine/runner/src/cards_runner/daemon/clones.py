"""Throwaway clones: the worker's, the daemon's integration clone, the verifier's.

Why clones and not `git worktree add`: a worktree shares the project's
`.git` (config, hooks, objects) with the worker, so anything model-run
code wrote there was read by the daemon's next git call in the project.
A clone has its own `.git`. Each attempt gets three:

1. **Worker clone** (`ClaimedCard.worktree_path`, under the run dir), made
   at a pinned `base_sha` before the worker starts. The daemon never runs
   git there again. The worker hands its changes back as a patch file
   (data, `PATCH_NAME`), not as commits for the daemon to read.
2. **Integration clone** (`clones/<attempt>/integration`, daemon state),
   made only after the worker's whole job has ended. The daemon applies
   the patch to the index at `base_sha`, checks every resulting path and
   mode as git itself records them, and commits once, with the identity
   pinned at boot (`git_safety.commit_identity`). Its sha is the only
   thing the verifier checks and the merge gate pushes.
3. **Verify clone** (`clones/<attempt>/verify`), detached at that sha, for
   the verifier's model-written shell ACs. The daemon runs git there only
   to create it.

All clones use `--no-hardlinks` (a hardlinked object file is the owner's
object file) and `--single-branch --no-tags`. A daemon clone is trusted
in memory the moment `git clone` returns (`git_safety.trust_daemon_clone`)
and checked before every later call, like any repo.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Final

from ..common import git_safety
from ..common.git_safety import resolved, run_git
from ..common.types import PATCH_DONE_NAME, PATCH_NAME, RuntimePaths


log = logging.getLogger(__name__)

# A card's change is source code. Anything bigger is refused, not applied.
PATCH_MAX_BYTES: Final[int] = 20 * 1024 * 1024
# What the patch's binary hunks may inflate to, and how many files it may touch.
PATCH_MAX_INFLATED_BYTES: Final[int] = 64 * 1024 * 1024
PATCH_MAX_FILES: Final[int] = 2000
CLONE_TIMEOUT_SEC: Final[float] = 300.0
_DIFF_HEADER_RE: Final[re.Pattern[bytes]] = re.compile(rb"^diff --git ", re.M)
_BINARY_SIZE_RE: Final[re.Pattern[bytes]] = re.compile(rb"^(literal|delta) ([0-9]+)\r?$", re.M)

_SYMLINK_MODE: Final[str] = "120000"
_GITLINK_MODE: Final[str] = "160000"
_SHA_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_SHORT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"~[0-9]")


class CloneError(Exception):
    """A clone could not be made or checked. The attempt cannot proceed."""


class PatchRejected(Exception):
    """The worker's patch is missing, malformed, oversized, or touches a
    path or file mode the daemon will not commit."""


def clones_root(paths: RuntimePaths) -> Path:
    return paths.todo_root / ".cards-runner-state" / "clones"


def attempt_clone_dir(paths: RuntimePaths, attempt_trace_id: str) -> Path:
    return clones_root(paths) / attempt_trace_id


def _git(cwd: Path, *args: str, extra_config: tuple[tuple[str, str], ...] = (),
         first_sight_ok: bool = False) -> str:
    proc = run_git([resolved("git"), *args], cwd=cwd, timeout=CLONE_TIMEOUT_SEC,
                   first_sight_ok=first_sight_ok, extra_config=extra_config)
    if proc.returncode != 0:
        raise CloneError(f"git {args[0]} failed in {cwd}: rc={proc.returncode} "
                         f"stderr={(proc.stderr or '').strip()[:400]!r}")
    if proc.stdout is None:  # a reader thread died: never parse a guess
        raise CloneError(f"git {args[0]} in {cwd} returned no output")
    return proc.stdout


def _clone(source: Path, dest: Path, *, pins: git_safety._Pins, branch: str,
           checkout: bool = True, first_sight_ok: bool = False) -> None:
    """`git clone` run FROM the (trusted) source; pin the result, then trust it."""
    if dest.exists():
        raise CloneError(f"clone destination {dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--quiet", "--no-hardlinks", "--no-tags", "--single-branch",
            "--branch", branch]
    if not checkout:
        args.append("--no-checkout")
    # A file:// URL, not a path: a local-path clone copies objects/ wholesale,
    # so --single-branch kept other branches' REFS out but not their objects,
    # which a worker could read by sha (#87 deep verify, tests lens note).
    # The pack transport sends only what the cloned branch reaches.
    _git(source, *args, "--", Path(os.path.abspath(source)).as_uri(), str(dest),
         first_sight_ok=first_sight_ok)
    # Pinned before it is trusted: from here to release, `dest` and its
    # `.git` cannot be renamed or replaced by any process (L4-1).
    pins.pin(dest)
    pins.pin(dest / ".git")
    git_safety.trust_daemon_clone(dest)


def _pin_ancestors(pins: git_safety._Pins, path: Path, levels: int) -> None:
    """Pin `path` and its parents, top-down, so nothing above a clone can be
    swapped either (attempt dir, clones root, state dir for a daemon clone)."""
    chain = [path, *list(path.parents)[:levels - 1]]
    for directory in reversed(chain):
        pins.pin(directory)


def resolve_base_sha(project_dir: Path, base_branch: str) -> str:
    """Pin the base: a branch name can move between clone and push."""
    sha = _git(project_dir, "rev-parse", "--verify", "--end-of-options",
               f"{base_branch}^{{commit}}", first_sight_ok=True).strip()
    if not _SHA_RE.match(sha):
        raise CloneError(f"unexpected rev-parse output for {base_branch!r}: {sha!r}")
    return sha


def prepare_worker_clone(*, project_dir: Path, base_branch: str, branch: str,
                         dest: Path) -> str:
    """Make the worker's clone at the base sha, on `branch`. Returns the sha.

    The clone has no remote (the worker has nothing to push to) and the
    daemon forgets it once this returns.
    """
    if not (project_dir / ".git").exists():
        raise CloneError(f"project_dir {project_dir} is not a git repo")
    base_sha = resolve_base_sha(project_dir, base_branch)
    with git_safety.pinned_clones() as pins:
        try:
            _clone(project_dir, dest, pins=pins, branch=base_branch, first_sight_ok=True)
            _git(dest, "checkout", "--quiet", "-b", branch, base_sha)
            _git(dest, "remote", "remove", "origin")
        finally:
            # From here on the clone belongs to the worker.
            git_safety.forget(dest)
            pins.release(dest / ".git")
            pins.release(dest)
    return base_sha


def _project_origin_url(project_dir: Path) -> str | None:
    proc = run_git([resolved("git"), "config", "--get", "remote.origin.url"],
                   cwd=project_dir, first_sight_ok=True)
    url = proc.stdout.strip()
    return url if proc.returncode == 0 and url else None


def read_patch(run_dir: Path, *, max_bytes: int = PATCH_MAX_BYTES) -> bytes:
    """The worker's finished patch, or PatchRejected. Read once, capped."""
    if not (run_dir / PATCH_DONE_NAME).is_file():
        raise PatchRejected("the worker did not finish writing a patch")
    path = run_dir / PATCH_NAME
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise PatchRejected(f"could not read the worker's patch: {exc}") from exc
    if len(data) > max_bytes:
        raise PatchRejected(f"patch exceeds {max_bytes} bytes")
    marker = (run_dir / PATCH_DONE_NAME).read_text(encoding="utf-8", errors="replace")
    if f"sha256={hashlib.sha256(data).hexdigest()}" not in marker.split():
        raise PatchRejected("patch does not match its done marker (cut off or rewritten)")
    return data


def path_allowed(path: str) -> bool:
    """A repo-relative path the daemon will commit.

    No absolute or drive paths, no `..`, no backslash or colon (NTFS
    streams, drive letters), and no component that Windows or macOS would
    treat as `.git`: case-folded after stripping trailing dots and spaces,
    and no 8.3 short names (`GIT~1`). git's own protectNTFS/protectHFS
    checks run too; this is the daemon's, independent of git's version.
    """
    if not path or path.startswith("/") or "\\" in path or ":" in path or "\x00" in path:
        return False
    for part in path.split("/"):
        if part in ("", ".", ".."):
            return False
        folded = part.rstrip(" .").casefold()
        if folded in ("", ".git") or _SHORT_NAME_RE.search(part):
            return False
    return True


def _parse_raw(out: str) -> list[tuple[str, str]]:
    """(new_mode, path) pairs from `--raw -z --no-renames` output."""
    fields = out.split("\x00")
    changes: list[tuple[str, str]] = []
    i = 0
    while i + 1 < len(fields) and fields[i]:
        meta, path = fields[i], fields[i + 1]
        parts = meta.lstrip(":").split()
        if len(parts) < 5:
            raise PatchRejected(f"unparsable index entry {meta!r}")
        changes.append((parts[1], path))
        i += 2
    return changes


def _refuse_bad_entries(changes: list[tuple[str, str]]) -> None:
    for mode, path in changes:
        if not path_allowed(path):
            raise PatchRejected(f"patch touches a refused path: {path!r}")
        if mode in (_SYMLINK_MODE, _GITLINK_MODE):
            raise PatchRejected(f"patch adds a symlink or submodule: {path!r} ({mode})")


def integrate_patch(*, project_dir: Path, base_branch: str, base_sha: str,
                    branch: str, patch_file: Path, dest: Path, message: str) -> str:
    """Apply the worker's patch in a fresh daemon clone; commit; return the sha.

    The patch is applied to the INDEX only (`--cached`): no file the patch
    names is written to disk here. Every changed path and its new mode is
    read back from git and refused if it is not a plain relative path or if
    it is a symlink or submodule: once in the index before committing (an
    early refusal), and once in the COMMITTED TREE, the check that counts.
    The index is not part of the trust fingerprint, and with
    `max_parallel > 1` another card's worker (same user) is alive while
    this runs and could rewrite it between check and commit. The commit's
    tree cannot change under the sha that is pushed. An empty patch leaves
    the base sha (and the merge gate's PR then has nothing in it, exactly
    as an executor that committed nothing did before).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    with git_safety.pinned_clones() as pins:
        # Held until the caller's pin set ends (the daemon: after the push).
        _pin_ancestors(pins, dest.parent, 3)
        _clone(project_dir, dest, pins=pins, branch=base_branch, first_sight_ok=True)
        return _integrate(project_dir=project_dir, base_sha=base_sha, branch=branch,
                          patch_file=patch_file, dest=dest, message=message)


def _integrate(*, project_dir: Path, base_sha: str, branch: str, patch_file: Path,
               dest: Path, message: str) -> str:
    _git(dest, "checkout", "--quiet", "-b", branch, base_sha)
    origin = _project_origin_url(project_dir)
    if origin is not None:
        _git(dest, "remote", "set-url", "origin", origin)
    else:
        _git(dest, "remote", "remove", "origin")
    # The baseline was taken before these, the daemon's own config writes;
    # take it again now. Nothing from THIS attempt is alive (its worker's
    # job has ended; its verifier has not started). A sibling attempt's
    # worker could write files here by absolute path (the declared
    # same-user residual, RUNNER_CONTRACT.md "What this does not isolate"),
    # but cannot swap this dir or its .git for a link: they are pinned
    # (git_safety "daemon clones"), and every call re-checks the pairing.
    git_safety.trust_daemon_clone(dest)
    if patch_file.stat().st_size == 0:
        return base_sha
    _refuse_oversized(patch_file.read_bytes())
    proc = run_git([resolved("git"), "apply", "--cached", "--binary", "--whitespace=nowarn",
                    "--", str(patch_file)], cwd=dest, timeout=CLONE_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise PatchRejected(f"git apply refused the patch: {proc.stderr.strip()[:400]}")
    changes = _parse_raw(_git(dest, "diff-index", "--cached", "--raw", "-z",
                              "--no-renames", base_sha))
    _refuse_bad_entries(changes)
    if not changes:
        return base_sha
    _git(dest, "commit", "--quiet", "--no-verify", "-m", message,
         extra_config=tuple(git_safety.commit_identity()))
    sha = _git(dest, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if not _SHA_RE.match(sha):
        raise CloneError(f"unexpected HEAD after commit: {sha!r}")
    # What is pushed is `sha`: check its parent and its tree, not the index.
    parents = _git(dest, "rev-list", "--parents", "-n", "1", sha).split()
    if parents != [sha, base_sha]:
        raise PatchRejected(f"integrated commit is not one commit on {base_sha[:12]}")
    _refuse_bad_entries(_parse_raw(_git(dest, "diff-tree", "-r", "--raw", "-z",
                                        "--no-renames", base_sha, sha)))
    new = _case_collisions(_tree_paths(dest, sha)) - _case_collisions(_tree_paths(dest, base_sha))
    if new:
        raise PatchRejected(f"patch adds paths that collide on a case-insensitive "
                            f"filesystem: {sorted(new)[:5]}")
    return sha


def _refuse_oversized(patch: bytes) -> None:
    """Refuse before git inflates anything (#87 deep verify patch-F3): a
    binary hunk states its inflated size, and 6 MB of patch inflated to 1 GiB."""
    files = len(_DIFF_HEADER_RE.findall(patch))
    if files > PATCH_MAX_FILES:
        raise PatchRejected(f"patch touches {files} files, over {PATCH_MAX_FILES}")
    inflated = sum(int(m.group(2)) for m in _BINARY_SIZE_RE.finditer(patch))
    if inflated > PATCH_MAX_INFLATED_BYTES:
        raise PatchRejected(f"binary hunks inflate to {inflated} bytes, "
                            f"over {PATCH_MAX_INFLATED_BYTES}")


def _tree_paths(dest: Path, sha: str) -> list[str]:
    out = _git(dest, "ls-tree", "-r", "-z", "--name-only", "--full-tree", sha)
    return [p for p in out.split("\x00") if p]


def _case_collisions(paths: list[str]) -> set[str]:
    """Normalized names (files and every directory above them) that two
    different paths share under casefold plus NFC (#87 deep verify patch-F1:
    README.md and Readme.md both committed, while the NTFS verify clone held
    only one, so the verifier checked a different tree than was pushed)."""
    seen: dict[str, str] = {}
    collisions: set[str] = set()
    for path in paths:
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            original = "/".join(parts[:i])
            key = unicodedata.normalize("NFC", original).casefold()
            if seen.setdefault(key, original) != original:
                collisions.add(key)
    return collisions


def prepare_verify_clone(*, integration: Path, branch: str, sha: str, dest: Path) -> None:
    """A clone of the integration clone, detached at exactly `sha`.

    The verifier's shell ACs (often model-written tests) run here, so the
    daemon forgets this clone once HEAD is confirmed and never runs git in
    it again.
    """
    with git_safety.pinned_clones() as pins:
        try:
            _clone(integration, dest, pins=pins, branch=branch, checkout=False)
            _git(dest, "checkout", "--quiet", "--detach", sha)
            head = _git(dest, "rev-parse", "--verify", "HEAD^{commit}").strip()
            if head != sha:
                raise CloneError(f"verify clone HEAD {head} is not {sha}")
            # No path back to the integration clone for an AC to push into.
            _git(dest, "remote", "remove", "origin")
        finally:
            git_safety.forget(dest)
            # The ACs own this clone now; only the daemon's use was pinned.
            pins.release(dest / ".git")
            pins.release(dest)


def _make_writable(func, path, _exc) -> None:  # noqa: ANN001 - shutil callback
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    """rmtree that also removes git's read-only object files on Windows."""
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable)
    else:  # pragma: no cover - 3.11
        shutil.rmtree(path, onerror=_make_writable)


def remove_attempt_clones(paths: RuntimePaths, attempt_trace_id: str) -> None:
    """Best-effort removal of an attempt's daemon clones."""
    root = attempt_clone_dir(paths, attempt_trace_id)
    for name in ("integration", "verify"):
        git_safety.forget(root / name)
    try:
        remove_tree(root)
    except OSError as exc:
        log.warning("could not remove clones for %s: %s", attempt_trace_id, exc)


def reap_stale_clones(paths: RuntimePaths, in_flight_attempts: set[str]) -> int:
    """Remove clone dirs of attempts that are no longer in flight."""
    root = clones_root(paths)
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.name in in_flight_attempts or not child.is_dir():
            continue
        remove_attempt_clones(paths, child.name)
        removed += 0 if child.exists() else 1
    return removed


__all__ = [
    "CloneError", "PATCH_MAX_BYTES", "PatchRejected",
    "attempt_clone_dir", "clones_root", "integrate_patch",
    "path_allowed", "prepare_verify_clone", "prepare_worker_clone", "read_patch",
    "reap_stale_clones", "remove_attempt_clones", "remove_tree", "resolve_base_sha",
]
