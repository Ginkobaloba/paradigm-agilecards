"""Running git in a worktree a model has written to, without running the model's code.

Git runs programs named in config and in hooks: `core.hooksPath` and
`hooks/*`, `core.fsmonitor`, `credential.helper`, `core.sshCommand`,
`core.askPass`, `diff.external`, textconv drivers, and more. In tool mode
the executor can write the repo's config and hooks (its `shell` tool is
not confined to git verbs), so any git the DAEMON then runs in that
worktree, `git push` for the merge gate, `git diff` for the shadow
ledger, could execute model-written code with the daemon's environment.

Two layers, because a list of dangerous keys is never complete:

1. `hardened_git_env` -- the env for every daemon git/gh call in a card
   worktree. It is a scrubbed allowlist env (no Anthropic or cloud keys;
   only the gh credentials gh needs), plus `GIT_CONFIG_COUNT` overrides,
   which outrank every config file, neutralising the known exec keys.
2. `git_exec_surface` -- a fingerprint of the repo's config files and
   hooks. The daemon takes it at claim and again at worker exit; any
   change at all routes the card to `blocked` before the daemon runs
   git there. That catches keys the overrides do not name, and it
   protects the HUMAN too: a hook planted in the shared repo would
   otherwise fire on the owner's next manual push.

Same-user processes on Windows are not isolated from each other, so this
does not make tool mode safe against a determined payload (it could read
another process's memory). It closes the env and config routes.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .env_scrub import scrub_environment

# gh authenticates from these (or from its config dir under the user
# profile, which the scrub keeps). Nothing else credential-like passes.
GH_ENV_KEEP: tuple[str, ...] = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_HOST", "GH_CONFIG_DIR",
)

_EMPTY_HOOKS: Path | None = None


def empty_hooks_dir() -> Path:
    """A directory with no hooks in it, for `core.hooksPath`.

    Recreated if anything has appeared in it, so a planted file there is
    never run either.
    """
    global _EMPTY_HOOKS
    if _EMPTY_HOOKS is None or not _EMPTY_HOOKS.is_dir() or any(_EMPTY_HOOKS.iterdir()):
        _EMPTY_HOOKS = Path(tempfile.mkdtemp(prefix="cards-runner-nohooks-"))
    return _EMPTY_HOOKS


def git_config_overrides() -> list[tuple[str, str]]:
    """Command-line-precedence config for every daemon git in a card worktree."""
    return [
        ("core.hooksPath", str(empty_hooks_dir())),
        ("core.fsmonitor", "false"),
        ("core.askPass", ""),
        ("diff.external", ""),
        ("protocol.ext.allow", "never"),
    ]


def hardened_git_env(
    base: Mapping[str, str] | None = None, *, keep_gh_credentials: bool = True
) -> dict[str, str]:
    """The env for a daemon git/gh call in a model-touched worktree."""
    env = scrub_environment(
        base=dict(base if base is not None else os.environ),
        extra_keep=GH_ENV_KEEP if keep_gh_credentials else (),
    )
    overrides = git_config_overrides()
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for i, (key, value) in enumerate(overrides):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git_dirs(worktree: Path, git_path: str) -> tuple[Path, Path] | None:
    """`(git_dir, common_dir)` for `worktree`, or None if not a git checkout."""
    try:
        proc = subprocess.run(
            [git_path, "-C", str(worktree), "rev-parse", "--git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=15, check=False,
            env=hardened_git_env(keep_gh_credentials=False),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or len(lines) < 2:
        return None
    git_dir, common = (Path(p) for p in lines[:2])
    if not git_dir.is_absolute():
        git_dir = worktree / git_dir
    if not common.is_absolute():
        common = worktree / common
    return git_dir.resolve(), common.resolve()


def git_exec_surface(worktree: Path, *, git_path: str = "git") -> str | None:
    """Fingerprint of everything in the repo that can make git run a program.

    Covers the shared `config`, the per-worktree `config.worktree`, and
    every non-sample file under the shared and per-worktree `hooks/`.
    Read directly from disk, never through git, so taking it runs nothing.
    None when `worktree` is not a git checkout.
    """
    dirs = _git_dirs(worktree, git_path)
    if dirs is None:
        return None
    git_dir, common = dirs
    digest = hashlib.sha256()
    candidates = [common / "config", git_dir / "config.worktree", common / "config.worktree"]
    for hooks in (common / "hooks", git_dir / "hooks"):
        if hooks.is_dir():
            candidates += sorted(p for p in hooks.rglob("*") if p.is_file() and p.suffix != ".sample")
    for path in sorted(set(candidates)):
        digest.update(str(path).encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes())
        except FileNotFoundError:
            digest.update(b"<absent>")
        digest.update(b"\0")
    return digest.hexdigest()
