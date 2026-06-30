"""Per-worker environment scrubbing.

Per RUNNER_CONTRACT.md "Worktree isolation and cross-contamination
defense" item 1, each spawned executor gets a clean env block. No
credentials inherited unless the project config explicitly opts in.

Chunk 1's stub worker has the same scrub policy as chunk 2's real
worker. We do not want to discover a leakage bug only after the SDK
ships.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterable


# Variables we drop unconditionally. The runner's done criterion for
# chunk 1 names this exact set; do not change without updating the
# corresponding test in `tests/test_env_scrub.py`.
_DROP_EXACT: frozenset[str] = frozenset({
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "_NT_SYMBOL_PATH",
})

_DROP_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_",
    "OPENAI_",
    "AWS_",
    "AZURE_",
    "GCP_",
    "GOOGLE_APPLICATION_",
    "STRIPE_",
    "SLACK_",
)

# Variables we always keep (when present). Path-like and shell
# correctness essentials. Anything not in this list and not in the
# per-project preserve list gets dropped.
_KEEP_EXACT_POSIX: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SHELL",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
})

_KEEP_EXACT_WINDOWS: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMDATA",
    "APPDATA",
    "LOCALAPPDATA",
    "PUBLIC",
    "ALLUSERSPROFILE",
    "USERNAME",
    "USERDOMAIN",
    "COMPUTERNAME",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
})


def _platform_keep() -> frozenset[str]:
    return _KEEP_EXACT_WINDOWS if sys.platform == "win32" else _KEEP_EXACT_POSIX


def _normalize(name: str) -> str:
    """Windows env vars are case-insensitive; POSIX is case-sensitive.

    We normalize on Windows so a leaked `anthropic_api_key` (lowercase)
    is caught by the upper-cased prefix check.
    """
    return name.upper() if sys.platform == "win32" else name


def scrub_environment(
    *,
    base: dict[str, str] | None = None,
    extra_drop: Iterable[str] = (),
    extra_keep: Iterable[str] = (),
    add: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a clean env block suitable for `subprocess.Popen(env=...)`.

    `base` defaults to `os.environ`. The caller can pass an empty
    dict if it wants the absolute minimum.

    `extra_drop` is the per-project additional scrub list. Matches
    both exact names and prefixes (entries ending in `_` are treated
    as prefix patterns).

    `extra_keep` is the per-project preserve list. Always honored
    regardless of the drop policy, so a project can intentionally
    pass through a specific variable.

    `add` is the per-worker injection (e.g., `ANTHROPIC_API_KEY` in
    chunk 2, `CARDS_RUNNER_*` in chunk 1).
    """
    base_env = dict(base if base is not None else os.environ)
    keep = set(_platform_keep())
    keep.update(extra_keep)

    extra_drop_exact: set[str] = set()
    extra_drop_prefixes: list[str] = []
    for entry in extra_drop:
        if entry.endswith("_") or entry.endswith("*"):
            extra_drop_prefixes.append(entry.rstrip("*").rstrip("_") + "_")
        else:
            extra_drop_exact.add(entry)

    keep_norm = {_normalize(k) for k in keep}

    out: dict[str, str] = {}
    for raw_name, value in base_env.items():
        norm = _normalize(raw_name)
        if norm in keep_norm:
            out[raw_name] = value
            continue
        if norm in _DROP_EXACT:
            continue
        if _normalize_any(norm) in {_normalize(x) for x in extra_drop_exact}:
            continue
        if any(norm.startswith(_normalize(pfx)) for pfx in _DROP_PREFIXES):
            continue
        if any(norm.startswith(_normalize(pfx)) for pfx in extra_drop_prefixes):
            continue
        # Default policy: drop. The scrubbed env is strictly an
        # allowlist of platform essentials plus project preserves
        # plus runner-injected values. Anything else is suspect.
    if add:
        out.update(add)
    return out


def _normalize_any(name: str) -> str:
    # Already-normalized name passthrough; kept as a separate helper
    # so the `any()` calls in `scrub_environment` stay readable.
    return name
