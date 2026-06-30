"""Handler: command.

Executes a shell command and asserts on its exit code. The
load-bearing handler for the verifier: most non-trivial cards
declare at least one `command` AC item (test runs, lint, build).

# Security posture (v1.3, locked).
#
# The threat model is: prevent accidents and casual misuse. Do not
# pretend to stop a determined attacker. The card author is a
# trusted-but-fallible planner agent. The same code that the
# verifier runs here is what the executor agent ran during the card.
# Defense in depth at the verifier layer is hygiene, not isolation.
#
# Posture validated against a sonnet second-opinion pass on
# 2026-05-18 (Drew's directive: capture the security reasoning in
# the file). The relevant choices:
#
# 1. `shell=False` ALWAYS. Commands declared as strings are split via
#    `shlex.split(posix=...)`; commands declared as lists are passed
#    through. This blocks the entire class of "card author typed
#    `&& rm -rf` and it executed in a shell" mistakes. Card authors
#    who need shell features (pipes, redirection) declare the command
#    as `["bash", "-c", "..."]` explicitly.
#
# 2. `stdin=DEVNULL`. A subprocess that blocks on stdin silently
#    eats the timeout. DEVNULL closes the door at the source.
#
# 3. `capture_output=True`. No tee to parent stdout. Verifier output
#    must be reproducible from the captured streams alone.
#
# 4. `encoding="utf-8", errors="replace"`. Compilers, native tools,
#    and Windows tooling routinely emit non-UTF-8 bytes. `errors=
#    "strict"` would crash the verifier on a passing card; `replace`
#    keeps us running and surfaces bad bytes visibly in evidence.
#
# 5. Process-group isolation. On POSIX: `start_new_session=True` so
#    timeout kills the whole tree via `killpg`. On Windows:
#    `CREATE_NEW_PROCESS_GROUP` so the equivalent kill semantics
#    apply.
#
# 6. `close_fds=True` everywhere. Default on POSIX in Python 3, but
#    set explicitly. On Windows the default is False; the verifier
#    forces True to avoid leaking handles into subprocesses.
#
# 7. Scrubbed env baseline. Preserve only the variables required for
#    common tools to function (PATH, HOME, USER, locale, TMPDIR,
#    PATHEXT and SYSTEMROOT on Windows, COMSPEC on Windows). Force
#    CI=true and NO_COLOR=1 so test runners produce machine-readable
#    output. Clear any variable matching the credential patterns
#    listed in `_CREDENTIAL_PATTERNS`: API keys, tokens, secrets,
#    cloud-provider creds, SSH agent forwarding, k8s and docker
#    sockets. Per-item `env` overrides merge into the baseline AFTER
#    scrubbing so a card author may re-add a credential explicitly
#    when their check genuinely needs one.
#
# 8. Timeout: per-item `timeout_sec` overrides the default of 60s.
#    On timeout: `proc.kill()` (which under POSIX kills the process
#    group when `start_new_session=True`; under Windows kills the
#    process group when `CREATE_NEW_PROCESS_GROUP` was set). The
#    handler waits for the child to actually exit before returning,
#    so the runner never has stranded zombies.
#
# Open holes deliberately not addressed at this layer:
#
# - Filesystem sandboxing. A `command` AC can `rm -rf` the worktree.
#   The runner is expected to give each verifier pass an ephemeral
#   worktree (see RUNNER_CONTRACT.md "Worktree isolation").
# - Network egress. A `command` can curl out. The verifier runs on
#   the same network as the executor; if a project needs egress
#   control, that lives in the worktree's network namespace, not
#   here.
# - Resource limits (memory, CPU, fork bombs). Out of scope; runners
#   that need them set cgroup limits at worktree creation.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult
from verifier.types import DEFAULT_TIMEOUTS_SEC, ACType


# Variables preserved in the scrubbed baseline. Anything not in this
# allow-list (and not matching the credential patterns below) is
# DROPPED rather than forwarded.
_POSIX_PRESERVE: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SHELL",
        "TZ",
    }
)
_WINDOWS_PRESERVE: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "USERNAME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TEMP",
        "TMP",
        "SHELL",
        "TZ",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "PROGRAMFILES",
        "PROGRAMDATA",
        "APPDATA",
        "LOCALAPPDATA",
        "WINDIR",
    }
)


# Forced values overlaid on top of the baseline. These deliberately
# override any value the parent had: we want determinism. CI=true
# flips most modern test runners and CLIs into non-interactive,
# machine-readable mode. NO_COLOR=1 suppresses ANSI escape sequences
# in captured output. TERM=dumb is the belt to NO_COLOR's
# suspenders for tools that ignore NO_COLOR. PYTHONUNBUFFERED=1
# avoids the "test passed but captured no output" surprise from
# pytest under capture.
_FORCED_ENV: dict[str, str] = {
    "CI": "true",
    "NO_COLOR": "1",
    "TERM": "dumb",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",
    "LC_ALL": "C.UTF-8",
}


# Regex patterns matched (case-insensitive) against env var names.
# A name matching ANY pattern is cleared from the baseline regardless
# of whether it appeared in the preserve list. Per-item `env`
# overrides re-add explicitly.
_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r".*_API_KEY$",
        r".*_TOKEN$",
        r".*_SECRET$",
        r".*_PASSWORD$",
        r"^ANTHROPIC_.*",
        r"^OPENAI_.*",
        r"^AWS_.*",
        r"^GCP_.*",
        r"^GOOGLE_.*",
        r"^AZURE_.*",
        r"^GH_TOKEN$",
        r"^GITHUB_TOKEN$",
        r"^NPM_TOKEN$",
        r"^PYPI_TOKEN$",
        r"^CARGO_REGISTRY_TOKEN$",
        r"^VAULT_.*",
        r"^SENTRY_AUTH_TOKEN$",
        r"^VERCEL_TOKEN$",
        r"^FLY_API_TOKEN$",
        r"^RAILWAY_TOKEN$",
        r"^DATABASE_URL$",
        r"^SSH_AUTH_SOCK$",
        r"^SSH_AGENT_PID$",
        r"^DOCKER_HOST$",
        r"^KUBECONFIG$",
        r"^GOOGLE_APPLICATION_CREDENTIALS$",
    )
)


_MAX_CAPTURED_BYTES: int = 16 * 1024


def run(
    item: Mapping[str, Any],
    *,
    worktree: Path,
    project_cfg: ProjectConfig,
) -> HandlerResult:
    cmd = _normalize_command(item["command"])
    expected = int(item.get("expected_exit_code", 0))
    cwd = _resolve_cwd(item.get("cwd"), worktree=worktree)
    env = _build_env(item.get("env") or {}, project_cfg=project_cfg)
    timeout = _resolve_timeout(item, project_cfg)

    popen_kwargs: dict[str, Any] = {
        "args": cmd,
        "cwd": str(cwd),
        "env": env,
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "close_fds": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    timed_out = False
    stdout = ""
    stderr = ""
    exit_code: int | None = None

    try:
        with subprocess.Popen(**popen_kwargs) as proc:  # type: ignore[arg-type]
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    # Belt-and-suspenders: if even kill+wait fails,
                    # let Popen.__exit__ reap and we surface what we
                    # have.
                    stdout, stderr = "", ""
                exit_code = proc.returncode
    except FileNotFoundError as exc:
        return HandlerResult(
            passed=False,
            evidence={
                "command": cmd,
                "cwd": str(cwd),
                "error": (
                    f"command binary not found: {exc.filename or cmd[0]}. "
                    "Check PATH and the scrubbed env baseline; add the "
                    "binary directory explicitly via the per-item env "
                    "override if needed."
                ),
            },
        )
    except OSError as exc:
        return HandlerResult(
            passed=False,
            evidence={
                "command": cmd,
                "cwd": str(cwd),
                "error": f"OSError launching subprocess: {exc}",
            },
        )

    stdout = _truncate(stdout)
    stderr = _truncate(stderr)

    if timed_out:
        return HandlerResult(
            passed=False,
            evidence={
                "command": cmd,
                "cwd": str(cwd),
                "timed_out": True,
                "timeout_sec": timeout,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
            },
        )

    passed = exit_code == expected
    return HandlerResult(
        passed=passed,
        evidence={
            "command": cmd,
            "cwd": str(cwd),
            "exit_code": exit_code,
            "expected_exit_code": expected,
            "stdout": stdout,
            "stderr": stderr,
        },
    )


def _normalize_command(raw: Any) -> list[str]:
    """Accept str or list[str]; return list[str].

    Strings are split with shlex. On Windows we set `posix=False` so
    backslashes in paths survive intact. Lists are accepted as-is
    after a type check.
    """
    if isinstance(raw, list):
        if not all(isinstance(x, str) for x in raw):
            raise TypeError("command list must contain only strings")
        if not raw:
            raise ValueError("command list is empty")
        return list(raw)
    if isinstance(raw, str):
        if not raw.strip():
            raise ValueError("command string is empty")
        if sys.platform == "win32":
            # posix=False keeps backslashes in paths intact, but it
            # also keeps the surrounding quotes around each token.
            # Strip them so CreateProcess gets a clean argv[0].
            return [_strip_outer_quotes(tok) for tok in shlex.split(raw, posix=False)]
        return shlex.split(raw)
    raise TypeError(
        f"command must be str or list[str]; got {type(raw).__name__}"
    )


def _strip_outer_quotes(tok: str) -> str:
    """Strip a single pair of matching outer quotes from a token.

    Windows-mode shlex preserves the quotes that wrapped a token in
    the original string; we want the unquoted value as argv to the
    subprocess. Mismatched or absent quotes pass through unchanged.
    """
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
        return tok[1:-1]
    return tok


def _resolve_cwd(cwd: str | None, *, worktree: Path) -> Path:
    if cwd is None:
        return worktree
    p = Path(cwd)
    return p if p.is_absolute() else (worktree / p)


def _build_env(
    overrides: Mapping[str, str],
    *,
    project_cfg: ProjectConfig,
) -> dict[str, str]:
    """Build the subprocess env from a scrubbed baseline + overrides.

    The baseline starts as a filtered view of `os.environ`, then has
    `_FORCED_ENV` overlaid, then `overrides` overlaid on top of that.
    Per-item overrides therefore win over forced values, which lets a
    card legitimately ask for (e.g.) `CI=false` if a test actually
    requires interactive mode. Cards re-adding cleared credentials
    do so via this same overlay.
    """
    if sys.platform == "win32":
        preserve = _WINDOWS_PRESERVE
    else:
        preserve = _POSIX_PRESERVE
    preserve = preserve | frozenset(project_cfg.additional_env_to_preserve)

    extra_scrub: list[re.Pattern[str]] = [
        re.compile(p, re.IGNORECASE)
        for p in project_cfg.additional_env_to_scrub
    ]

    baseline: dict[str, str] = {}
    for name, value in os.environ.items():
        if name not in preserve:
            continue
        if _matches_any(_CREDENTIAL_PATTERNS, name):
            continue
        if _matches_any(extra_scrub, name):
            continue
        baseline[name] = value

    # On Windows, mirror USERPROFILE into HOME so Python tools that
    # look up HOME find a sane value. We do this on the baseline, so
    # an explicit per-item override still wins.
    if sys.platform == "win32":
        if "HOME" not in baseline and "USERPROFILE" in baseline:
            baseline["HOME"] = baseline["USERPROFILE"]

    # Forced determinism.
    for k, v in _FORCED_ENV.items():
        baseline[k] = v

    # Per-item explicit overrides.
    for k, v in overrides.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise TypeError(
                f"env override entries must be str/str; got "
                f"{type(k).__name__}={type(v).__name__}"
            )
        baseline[k] = v

    return baseline


def _matches_any(patterns: list[re.Pattern[str]] | tuple[re.Pattern[str], ...], name: str) -> bool:
    return any(p.match(name) for p in patterns)


def _resolve_timeout(
    item: Mapping[str, Any],
    project_cfg: ProjectConfig,
) -> int:
    if "timeout_sec" in item:
        return int(item["timeout_sec"])
    project_override = project_cfg.type_timeout_overrides_sec.get(
        ACType.COMMAND.value
    )
    if project_override is not None:
        return int(project_override)
    default = DEFAULT_TIMEOUTS_SEC[ACType.COMMAND.value]
    assert default is not None, "command type must have a non-None default timeout"
    return default


def _truncate(s: str) -> str:
    if len(s.encode("utf-8", errors="replace")) <= _MAX_CAPTURED_BYTES:
        return s
    # Cut on a code-point boundary, not a byte boundary, so we don't
    # corrupt the trailing characters under errors="replace".
    return s[: _MAX_CAPTURED_BYTES // 2] + "\n... [truncated] ...\n" + s[-1024:]
