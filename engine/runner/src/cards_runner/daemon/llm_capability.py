"""Whether this daemon can make LLM calls, said out loud.

Without ANTHROPIC_API_KEY (or without the `anthropic` package) the daemon
still runs, but three features quietly change behavior:

  * subjective acceptance-criteria items route to standup review instead of
    being judged (`_build_subjective_client` returns None);
  * the sibling reviewer falls back to a static client that always says
    `comment`, so its marker lands but no auto-merge ever fires;
  * the amendment reviewer falls back the same way.

That degrade is correct behavior, but it used to be visible only as an INFO
line, so a daemon missing its key looked healthy. The daemon now logs a
WARNING at boot naming what degraded, and writes the same facts to
`<todo_root>/daemon_capabilities.json`, which `cards-runner status` reads.

The file holds booleans, the daemon pid and a fixed reason string. It never
holds a key, a key prefix or a key length.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KEY_VAR = "ANTHROPIC_API_KEY"

DEGRADED_FEATURES: tuple[str, ...] = (
    "subjective acceptance criteria route to standup review instead of being judged",
    "sibling reviewer always answers `comment`, so no auto-merge fires",
    "amendment reviewer always answers `comment`",
)


@dataclass(frozen=True)
class LlmCapability:
    key_present: bool
    sdk_installed: bool

    @property
    def live(self) -> bool:
        return self.key_present and self.sdk_installed

    @property
    def reason(self) -> str | None:
        if not self.key_present:
            return f"{KEY_VAR} not set in the daemon environment"
        if not self.sdk_installed:
            return "the `anthropic` package is not installed"
        return None


def detect(environ: Mapping[str, str] | None = None) -> LlmCapability:
    env = os.environ if environ is None else environ
    return LlmCapability(
        key_present=bool(env.get(KEY_VAR)),
        sdk_installed=importlib.util.find_spec("anthropic") is not None,
    )


def warning_text(cap: LlmCapability) -> str:
    """The boot WARNING for a degraded daemon. Empty when live."""
    if cap.live:
        return ""
    lines = [f"LLM reviews DEGRADED: {cap.reason}. Until that is fixed:"]
    lines += [f"  - {f}" for f in DEGRADED_FEATURES]
    lines.append(
        f"  Fix: set {KEY_VAR} in the daemon's environment, for example in a "
        "gitignored .env read at startup (see cards_runner/common/envfile.py)."
    )
    return "\n".join(lines)


def to_record(cap: LlmCapability, *, pid: int, now: float | None = None) -> dict[str, Any]:
    return {
        "pid": pid,
        "written_at": time.time() if now is None else now,
        "llm_reviews": "live" if cap.live else "degraded",
        "anthropic_key_present": cap.key_present,
        "anthropic_sdk_installed": cap.sdk_installed,
        "reason": cap.reason,
    }


def write_record(path: Path, record: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(record), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_for_daemon(path: Path, daemon_pid: int | None) -> dict[str, Any] | None:
    """The record, but only if the RUNNING daemon wrote it.

    A file left by an earlier daemon describes a process that is gone, and
    reporting its `live` would be exactly the false health this module exists
    to remove. Returns None when there is no running daemon, no file, an
    unreadable file, or a pid mismatch.
    """
    if daemon_pid is None:
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("pid") != daemon_pid:
        return None
    return record
