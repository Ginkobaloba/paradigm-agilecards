"""Every git/gh call the daemon makes, through one hardened runner.

Threat model: prompt injection through card text or repo files steers a
tool-mode executor. It can write anything its user can: the repo's shared
`.git` (config, hooks), the user's global git config, TEMP. Git runs
programs named in config and hooks (hooks, `core.fsmonitor`,
`credential.helper`, `core.sshCommand`, `receivepack`, filter and diff
drivers, ...), and a list of such keys is never complete. So instead of
naming keys, the daemon's git never reads config the executor can write
without first proving it unchanged:

1. **One runner.** `run_git` is the only way daemon code runs git or gh
   (worktree prep and teardown, push, PR calls, the ledger diff). A test
   pins that no call site escapes it.
2. **No user or system config.** `GIT_CONFIG_GLOBAL` and
   `GIT_CONFIG_SYSTEM` point at empty files, so a write to `~/.gitconfig`
   (the executor's shell has a HOME) is never read by the daemon. gh's
   credential helper, which normally lives there, is pinned explicitly.
3. **Overrides for repo config.** `GIT_CONFIG_COUNT` entries outrank every
   config file: an empty `core.hooksPath`, `core.fsmonitor=false`, the
   credential helper reset, and a few more.
4. **A persisted baseline.** The repo's own config and hooks are
   fingerprinted the first time the daemon sees the repo (or when an
   operator runs `cards-runner trust-repo`), stored daemon-side, and
   checked before EVERY call. A mismatch QUARANTINES the repo: the call
   is refused and no further card from it runs until a human re-trusts
   it. `[branch "..."]` sections are excluded from the hash: the daemon's
   own `push -u` writes them, and they name no program.

The private files (empty configs, empty hooks dir, the trust store) live
in a daemon state dir, not TEMP, and are re-verified before every use,
because same-user files are never truly private.

What this does NOT close: same-user processes on Windows are not isolated,
so a determined payload could read another process's memory or write
the trust store directly. That needs OS-level isolation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .env_scrub import scrub_environment

log = logging.getLogger(__name__)

# gh authenticates from these (or from its config dir under the user
# profile, which the scrub keeps). Nothing else credential-like passes.
GH_ENV_KEEP: tuple[str, ...] = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_HOST", "GH_CONFIG_DIR",
)

GIT_TIMEOUT_SEC = 120.0

_STATE_DIR: Path | None = None
_LOCK = threading.Lock()


class RepoQuarantined(RuntimeError):
    """A repo's git config or hooks no longer match its trusted baseline."""

    def __init__(self, common_dir: Path, reason: str) -> None:
        super().__init__(f"repository {common_dir} is quarantined: {reason}")
        self.common_dir = common_dir
        self.reason = reason


def state_dir_for(todo_root: Path) -> Path:
    """Where a daemon rooted at `todo_root` keeps this module's state."""
    return Path(todo_root) / ".cards-runner-state" / "git"


def configure(state_dir: Path) -> None:
    """Point the runner at the daemon's state dir (called at daemon boot)."""
    global _STATE_DIR
    _STATE_DIR = Path(state_dir)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)


def _state_dir() -> Path:
    if _STATE_DIR is None:
        # Library use without a daemon (tests, doctor): a per-process dir.
        import tempfile

        configure(Path(tempfile.mkdtemp(prefix="cards-runner-git-")))
    assert _STATE_DIR is not None
    return _STATE_DIR


def _empty_file(name: str) -> Path:
    """A zero-byte file, verified (and reset) immediately before each use."""
    path = _state_dir() / name
    if not path.is_file() or path.stat().st_size != 0:
        path.write_bytes(b"")
    return path


def empty_hooks_dir() -> Path:
    """A hooks dir with nothing in it, verified (and emptied) before each use."""
    path = _state_dir() / "nohooks"
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()
    path.mkdir(exist_ok=True)
    return path


def git_config_overrides(*, gh_path: str | None = None) -> list[tuple[str, str]]:
    """Command-line-precedence config for every daemon git call."""
    overrides = [
        ("core.hooksPath", str(empty_hooks_dir())),
        ("core.fsmonitor", "false"),
        ("core.askPass", ""),
        ("diff.external", ""),
        ("protocol.ext.allow", "never"),
        # An empty value resets the helper list built from every config
        # file, so a repo-set helper is dropped ...
        ("credential.helper", ""),
    ]
    if gh_path:
        # ... and gh's own helper (normally in the now-unread global
        # config) is pinned back explicitly, so pushes still authenticate.
        overrides.append(("credential.helper", f'!"{gh_path}" auth git-credential'))
    return overrides


def hardened_git_env(
    base: Mapping[str, str] | None = None,
    *,
    keep_gh_credentials: bool = True,
    gh_path: str | None = None,
) -> dict[str, str]:
    """The env for a daemon git/gh call: no keys, no user config, pinned overrides."""
    env = scrub_environment(
        base=dict(base if base is not None else os.environ),
        extra_keep=GH_ENV_KEEP if keep_gh_credentials else (),
    )
    env["GIT_CONFIG_GLOBAL"] = str(_empty_file("empty-global.gitconfig"))
    env["GIT_CONFIG_SYSTEM"] = str(_empty_file("empty-system.gitconfig"))
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if keep_gh_credentials and gh_path is None:
        gh_path = shutil.which("gh")
    overrides = git_config_overrides(gh_path=gh_path if keep_gh_credentials else None)
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for i, (key, value) in enumerate(overrides):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


# ---- the baseline ---------------------------------------------------------


def _strip_branch_sections(config_text: str) -> str:
    """Config text without `[branch "..."]` sections (the daemon writes those)."""
    kept: list[str] = []
    skipping = False
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            skipping = stripped.lower().startswith("[branch")
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def common_dir_of(path: Path) -> Path | None:
    """The shared `.git` dir for a checkout or worktree root, or None.

    Read from disk, NOT via `git rev-parse`: asking git means running git
    in a repo the executor may have rewritten, before anything is checked.
    A worktree's `.git` file and its `commondir` file are executor-writable
    too, so the answer is only an input to `assert_trusted`, which refuses
    any common dir it was not told to trust.
    """
    dot_git = path / ".git"
    try:
        if dot_git.is_dir():
            return dot_git.resolve()
        if not dot_git.is_file():
            return None
        first = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        if not first.lower().startswith("gitdir:"):
            return None
        gitdir = Path(first[len("gitdir:"):].strip())
        if not gitdir.is_absolute():
            gitdir = path / gitdir
        commondir_file = gitdir / "commondir"
        if not commondir_file.is_file():
            return gitdir.resolve()
        common = Path(commondir_file.read_text(encoding="utf-8", errors="replace").strip())
        if not common.is_absolute():
            common = gitdir / common
        return common.resolve()
    except OSError:
        return None


def fingerprint(common_dir: Path) -> str:
    """Hash of what can make git run a program: shared config + hooks.

    Read straight from disk, never through git. `[branch]` sections are
    excluded (the daemon's own `push -u` writes them; they name no program).
    """
    digest = hashlib.sha256()
    for name in ("config", "config.worktree"):
        path = common_dir / name
        digest.update(name.encode() + b"\0")
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            digest.update(_strip_branch_sections(text).encode("utf-8"))
        else:
            digest.update(b"<absent>")
        digest.update(b"\0")
    hooks = common_dir / "hooks"
    if hooks.is_dir():
        for path in sorted(p for p in hooks.rglob("*") if p.is_file() and p.suffix != ".sample"):
            digest.update(str(path.relative_to(common_dir)).encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


@dataclass
class TrustRecord:
    baseline: str
    quarantined: bool = False
    reason: str = ""


def _store_path() -> Path:
    return _state_dir() / "git_trust.json"


def _load_store() -> dict[str, dict[str, object]]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_store(data: dict[str, dict[str, object]]) -> None:
    tmp = _store_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, _store_path())


def trust_record(common_dir: Path) -> TrustRecord | None:
    raw = _load_store().get(str(common_dir))
    if not isinstance(raw, dict) or not isinstance(raw.get("baseline"), str):
        return None
    return TrustRecord(str(raw["baseline"]), bool(raw.get("quarantined")), str(raw.get("reason", "")))


def trust_repo(common_dir: Path) -> str:
    """Record `common_dir`'s current state as trusted (operator action).

    The baseline is only as good as the repo was at this moment.
    """
    value = fingerprint(common_dir)
    with _LOCK:
        data = _load_store()
        data[str(common_dir)] = {"baseline": value, "quarantined": False, "reason": ""}
        _save_store(data)
    return value


def _quarantine(common_dir: Path, reason: str) -> None:
    with _LOCK:
        data = _load_store()
        entry = data.get(str(common_dir)) or {}
        entry.update({"quarantined": True, "reason": reason})
        data[str(common_dir)] = entry
        _save_store(data)
    log.error("QUARANTINED repository %s: %s", common_dir, reason)


class UntrustedRepo(RuntimeError):
    """A path resolves to a repo the daemon was never told to trust."""


def assert_trusted(path: Path, *, first_sight_ok: bool = False) -> Path | None:
    """Raise unless `path`'s repo still matches its trusted baseline.

    `first_sight_ok` is for a registered project dir only (the daemon's
    first touch, before any executor has run there): an unknown repo has
    its baseline recorded. Anywhere else (a card worktree) an unknown repo
    is refused, because the executor can repoint a worktree's `.git` file.
    Returns the common dir, or None when `path` has no `.git` at all (the
    caller's git then cannot walk up, see GIT_CEILING_DIRECTORIES).
    """
    common = common_dir_of(path)
    if common is None:
        return None
    record = trust_record(common)
    if record is None:
        if not first_sight_ok:
            raise UntrustedRepo(f"{path} resolves to {common}, which was never trusted")
        value = trust_repo(common)
        log.warning("first sight of repository %s: baseline %s recorded", common, value[:12])
        return common
    if record.quarantined:
        raise RepoQuarantined(common, record.reason or "quarantined earlier")
    if fingerprint(common) != record.baseline:
        reason = "git config or hooks changed since the repository was trusted"
        _quarantine(common, reason)
        raise RepoQuarantined(common, reason)
    return common


def run_git(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    keep_gh_credentials: bool = False,
    gh_path: str | None = None,
    timeout: float = GIT_TIMEOUT_SEC,
    first_sight_ok: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one daemon git or gh command, hardened. The only way the daemon does.

    `cwd` must be a repo root (a project dir or a card worktree). Before
    anything runs, its repo is checked against the trusted baseline
    (RepoQuarantined / UntrustedRepo on failure), and git is stopped from
    walking above `cwd` to find some other repo. `cwd=None` is for gh calls
    that touch no checkout.
    """
    env = hardened_git_env(keep_gh_credentials=keep_gh_credentials, gh_path=gh_path)
    if cwd is not None:
        assert_trusted(cwd, first_sight_ok=first_sight_ok)
        env["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).resolve().parent)
    return subprocess.run(
        list(argv), cwd=str(cwd) if cwd is not None else None,
        capture_output=True, text=True, timeout=timeout, check=False, env=env,
    )
