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
   `GIT_CONFIG_SYSTEM` point at the null device, so a write to `~/.gitconfig`
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
   it. Config is hashed as GIT parses it (never by a reader of our own,
   which could disagree with git), minus the `branch.<name>.remote/merge`
   keys the daemon's own `push -u` writes.

The empty hooks dir and the trust store live in a daemon state dir. Same-
user files are never truly private, so the hooks dir is re-emptied before
every use, and the trust store on disk is only a fallback: this process's
in-memory records are authoritative (see `_MEMORY`).

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
# The authoritative trust records for this process. The file is only a
# fallback for repos this process has not seen yet: an executor that
# deletes or rewrites it (it sits at a fixed path near every worktree)
# cannot re-arm "first sight" or lift a quarantine for a running daemon.
_MEMORY: dict[str, "TrustRecord"] = {}
# Bare binary names resolved ONCE, at configure() (daemon boot, before any
# executor ran), so a git.exe planted later in a user-writable PATH dir is
# not what the daemon's own calls run. The executor's tools still use PATH.
_PINNED_BINARIES: dict[str, str] = {}
_PINNABLE: tuple[str, ...] = ("git", "gh", "powershell.exe")
# The identity on the daemon's own commits (the one commit per card that
# integrates a worker's patch), read once at configure() from the owner's
# real config, before any executor ran. Model-chosen identities and
# commit messages never reach a pushed commit.
_IDENTITY: dict[str, str] = {}
_FALLBACK_IDENTITY: dict[str, str] = {"user.name": "cards-runner", "user.email": "cards-runner@localhost"}


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
    _MEMORY.clear()
    _PINNED_BINARIES.clear()
    for name in _PINNABLE:
        found = shutil.which(name)
        if found:
            _PINNED_BINARIES[name] = found
    _IDENTITY.clear()
    _IDENTITY.update(_read_identity())


def _read_identity() -> dict[str, str]:
    found: dict[str, str] = {}
    git = _PINNED_BINARIES.get("git")
    for key in ("user.name", "user.email"):
        value = None
        for scope in ("--global", "--system"):
            if git is None:
                break
            try:
                cp = subprocess.run([git, "config", scope, "--get", key], capture_output=True,
                                    text=True, timeout=10, check=False, env=scrub_environment())
            except (OSError, subprocess.SubprocessError):
                continue
            if cp.returncode == 0 and cp.stdout.strip():
                value = cp.stdout.strip()
                break
        found[key] = value or _FALLBACK_IDENTITY[key]
    return found


def commit_identity() -> list[tuple[str, str]]:
    """`extra_config` for a daemon-authored commit: the identity pinned at boot."""
    identity = _IDENTITY or _FALLBACK_IDENTITY
    return [("user.name", identity["user.name"]), ("user.email", identity["user.email"])]


def resolved(binary: str) -> str:
    """The boot-pinned absolute path for a bare `git`/`gh`/`powershell.exe`."""
    return _PINNED_BINARIES.get(binary, binary)


def _state_dir() -> Path:
    if _STATE_DIR is None:
        # Library use without a daemon (tests, doctor): a per-process dir.
        import tempfile

        configure(Path(tempfile.mkdtemp(prefix="cards-runner-git-")))
    assert _STATE_DIR is not None
    return _STATE_DIR


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
        # Refuse paths NTFS or HFS+ would alias to `.git` (`.GIT`, `git~1`,
        # `.git.`, ...) in any checkout or apply the daemon does.
        ("core.protectNTFS", "true"),
        ("core.protectHFS", "true"),
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
    extra_config: Sequence[tuple[str, str]] = (),
) -> dict[str, str]:
    """The env for a daemon git/gh call: no keys, no user config, pinned overrides."""
    env = scrub_environment(
        base=dict(base if base is not None else os.environ),
        extra_keep=GH_ENV_KEEP if keep_gh_credentials else (),
    )
    # The null device, not a file: nothing can be written into it between
    # a check and git's read.
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if keep_gh_credentials and gh_path is None:
        gh_path = _PINNED_BINARIES.get("gh") or shutil.which("gh")
    overrides = [*git_config_overrides(gh_path=gh_path if keep_gh_credentials else None),
                 *extra_config]
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for i, (key, value) in enumerate(overrides):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


# ---- the baseline ---------------------------------------------------------


# Keys the daemon's own `git push -u` and `gh pr create` write. They are
# excluded from the hash so the daemon does not quarantine itself; they
# name a remote and a ref, never a program. Every OTHER branch.* key
# (pushRemote, ...) is still hashed, and so is a `remote` whose value is
# not the NAME of a configured remote: a URL or path there is honoured by
# any remote-less push/fetch/pull (r3 W1).
_DAEMON_WRITTEN_BRANCH_KEYS: frozenset[str] = frozenset({"remote", "merge", "gh-merge-base"})

# One config entry as git reports it. `value` is None for a bare key
# (`[http] sslVerify`, i.e. true), which is not the same as `key =` ("").
ConfigEntry = tuple[str, "str | None"]


def _is_daemon_written(key: str, value: str | None, remote_names: frozenset[str]) -> bool:
    if not key.startswith("branch."):
        return False
    leaf = key.rsplit(".", 1)[-1]
    if leaf not in _DAEMON_WRITTEN_BRANCH_KEYS:
        return False
    return leaf != "remote" or value in remote_names


def _remote_names(entries: list[ConfigEntry]) -> frozenset[str]:
    """Remotes configured in these entries (`remote.<name>.url`)."""
    return frozenset(key[len("remote."):-len(".url")] for key, _ in entries
                     if key.startswith("remote.") and key.endswith(".url"))


def config_entries(path: Path) -> list[ConfigEntry] | None:
    """`path`'s entries exactly as git parses them, or None if git can't.

    Parsed BY GIT (`git config --file <path> --includes --null --list`),
    never by our own reader: any difference between the two parsers is a
    way to hide a key from the hash while git still honours it (#85 r2 N1:
    a lone CR). `git config --file` reads that file and what it includes;
    it runs no hooks or helpers. Unconditional `include.path` targets are
    expanded, so editing one changes the hash. `includeIf` targets are not:
    their conditions need a repository, which `--file` does not have.
    """
    env = hardened_git_env(keep_gh_credentials=False)
    env["GIT_CEILING_DIRECTORIES"] = str(_state_dir().parent)
    try:
        proc = subprocess.run(
            [resolved("git"), "config", "--file", str(path), "--includes", "--null", "--list"],
            cwd=str(_state_dir()), capture_output=True, timeout=GIT_TIMEOUT_SEC,
            check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    entries: list[ConfigEntry] = []
    for record in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if not record:
            continue
        key, sep, value = record.partition("\n")
        entries.append((key, value if sep else None))  # no separator: a bare key
    return entries

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


def _config_files(common_dir: Path) -> list[Path]:
    """Every config file git may read for this repo or one of its worktrees."""
    files = [common_dir / "config", common_dir / "config.worktree"]
    worktrees = common_dir / "worktrees"
    if worktrees.is_dir():
        files += sorted(worktrees.glob("*/config.worktree"))
    return files


def fingerprint(common_dir: Path) -> str:
    """Hash of what can make git run a program: config (as git parses it) + hooks.

    The daemon-written branch keys are excluded (see `_is_daemon_written`);
    which remotes count as configured is decided per file.
    A config file git cannot parse is hashed as raw bytes, so any edit to
    it still changes the hash. Per-worktree `config.worktree` files are
    included: with `extensions.worktreeConfig` set, git reads them too.
    """
    digest = hashlib.sha256()
    for path in _config_files(common_dir):
        digest.update(str(path.relative_to(common_dir)).encode() + b"\0")
        if not path.is_file():
            digest.update(b"<absent>\0")
            continue
        entries = config_entries(path)
        if entries is None:
            digest.update(b"<unparsed>\0" + path.read_bytes() + b"\0")
            continue
        remotes = _remote_names(entries)
        for key, value in entries:
            if _is_daemon_written(key, value, remotes):
                continue
            digest.update(key.encode("utf-8", "surrogateescape"))
            if value is None:
                digest.update(b"\x01")  # bare key: distinct from `key =`
            else:
                digest.update(b"\n" + value.encode("utf-8", "surrogateescape"))
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
    """This process's record for `common_dir`; the file only for unseen repos.

    So `cards-runner trust-repo` run from another process takes effect at
    the daemon's next start.
    """
    key = str(common_dir)
    with _LOCK:
        if key in _MEMORY:
            return _MEMORY[key]
        raw = _load_store().get(key)
        if not isinstance(raw, dict) or not isinstance(raw.get("baseline"), str):
            return None
        record = TrustRecord(str(raw["baseline"]), bool(raw.get("quarantined")),
                             str(raw.get("reason", "")))
        _MEMORY[key] = record
        return record


def trust_repo(common_dir: Path) -> str:
    """Record `common_dir`'s current state as trusted (operator action).

    The baseline is only as good as the repo was at this moment.
    """
    value = fingerprint(common_dir)
    with _LOCK:
        _MEMORY[str(common_dir)] = TrustRecord(value)
        data = _load_store()
        data[str(common_dir)] = {"baseline": value, "quarantined": False, "reason": ""}
        _save_store(data)
    return value


def trust_daemon_clone(path: Path) -> None:
    """Baseline a clone the daemon itself just made (this process only).

    Called right after `git clone` returns, before any other process is
    given the path. Never persisted: a restarted daemon has no use for an
    old attempt's clone, and the reaper removes it.
    """
    common = common_dir_of(path)
    if common is None:
        raise UntrustedRepo(f"{path} is not a repository after clone")
    value = fingerprint(common)
    with _LOCK:
        _MEMORY[str(common)] = TrustRecord(value)


def forget(path: Path) -> None:
    """Drop this process's record for a daemon clone about to be removed."""
    common = common_dir_of(path)
    if common is not None:
        with _LOCK:
            _MEMORY.pop(str(common), None)


def _quarantine(common_dir: Path, reason: str) -> None:
    with _LOCK:
        previous = _MEMORY.get(str(common_dir))
        _MEMORY[str(common_dir)] = TrustRecord(previous.baseline if previous else "", True, reason)
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
    extra_config: Sequence[tuple[str, str]] = (),
) -> subprocess.CompletedProcess[str]:
    """Run one daemon git or gh command, hardened. The only way the daemon does.

    `cwd` must be a repo root (a project dir or a card worktree). Before
    anything runs, its repo is checked against the trusted baseline
    (RepoQuarantined / UntrustedRepo on failure), and git is stopped from
    walking above `cwd` to find some other repo. `cwd=None` is for gh calls
    that touch no checkout.
    """
    env = hardened_git_env(keep_gh_credentials=keep_gh_credentials, gh_path=gh_path,
                           extra_config=extra_config)
    if cwd is not None:
        assert_trusted(cwd, first_sight_ok=first_sight_ok)
        env["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).resolve().parent)
    argv = [resolved(argv[0]), *argv[1:]] if argv else []
    return subprocess.run(
        argv, cwd=str(cwd) if cwd is not None else None,
        capture_output=True, text=True, timeout=timeout, check=False, env=env,
    )
