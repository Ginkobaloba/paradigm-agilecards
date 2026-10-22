"""Tiny stdlib `.env` reader, called from the CLI entry point only.

Ported from paradigm-provenance `provenance/envfile.py` (its #7, 2026-10-21),
which is the fleet pattern for repos that lost the User-scope
ANTHROPIC_API_KEY: a gitignored per-repo `.env` read into the process
environment at the entry point, so every consumer below it keeps reading
`os.environ` unchanged.

Accepted lines: `KEY=VALUE`, optionally prefixed with `export `. Blank lines
and `#` comment lines are skipped. A value may be wrapped in matching single
or double quotes; unquoted values drop a trailing ` # comment`. There is no
variable expansion and no escape processing: the text is taken literally.

Behavior contract:
  * Never overrides a variable that is already set in `os.environ`.
  * A missing or unreadable file is a no-op.
  * Malformed lines are skipped silently. Their content is NEVER echoed or
    logged, because a value on a bad line may be a secret.
  * The file is `$CARDS_RUNNER_ENV_FILE` when that is set (a PATH, never a
    secret), else `./.env` in the current working directory. The override
    exists because a daemon started by a scheduled task does not reliably
    start in the checkout.

Called from `cards_runner.cli.__main__.main` only, never at import, so
`import cards_runner` has no environment side effects.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ENV_FILE_VAR = "CARDS_RUNNER_ENV_FILE"

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_EXPORT = re.compile(r"export\s+")


def _parse_line(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    m = _EXPORT.match(line)
    if m:
        line = line[m.end():]
    key, sep, rest = line.partition("=")
    key = key.strip()
    if not sep or not _KEY.match(key):
        return None
    rest = rest.strip()
    if rest[:1] in ("'", '"'):
        quote = rest[0]
        end = rest.find(quote, 1)
        if end == -1:
            return None  # unterminated quote
        tail = rest[end + 1:].strip()
        if tail and not tail.startswith("#"):
            return None  # junk after the closing quote
        return key, rest[1:end]
    hash_at = re.search(r"\s#", rest)
    if hash_at:
        rest = rest[:hash_at.start()]
    return key, rest.strip()


def default_env_path() -> Path:
    """`$CARDS_RUNNER_ENV_FILE` if set, else `./.env`."""
    override = os.environ.get(ENV_FILE_VAR)
    return Path(override) if override else Path.cwd() / ".env"


def load_dotenv(path: str | os.PathLike[str] | None = None) -> int:
    """Load KEY=VALUE pairs from `path` (default `default_env_path()`).

    Existing variables win. Returns how many variables were newly set.
    """
    target = Path(path) if path is not None else default_env_path()
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return 0
    loaded = 0
    for raw in text.splitlines():
        parsed = _parse_line(raw)
        if parsed is None:
            continue
        key, value = parsed
        if key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded
