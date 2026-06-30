"""Env scrubbing: no `ANTHROPIC_*`, `OPENAI_*`, `AWS_*`, `GH_TOKEN`,
or `_NT_SYMBOL_PATH` reaches a sentinel worker.

We exercise two layers:

1. **Unit test on the scrub helper.** Pass in a polluted env, assert
   the result has none of the banned variables. This is the
   property-level guarantee.

2. **End-to-end via subprocess.** Spawn a sentinel worker that
   dumps `os.environ` to a JSON file inside the worktree. Assert the
   dumped env has no banned variables. This catches Windows-side
   inheritance surprises (env vars that `subprocess.Popen` re-injects
   despite an explicit `env=` argument).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common.env_scrub import scrub_environment


BANNED_EXACT = ("GH_TOKEN", "GITHUB_TOKEN", "_NT_SYMBOL_PATH")
BANNED_PREFIXES = (
    "ANTHROPIC_", "OPENAI_", "AWS_", "AZURE_", "GCP_", "STRIPE_", "SLACK_",
)


def _contains_banned(env: dict[str, str]) -> list[str]:
    found: list[str] = []
    for k in env:
        if k in BANNED_EXACT:
            found.append(k)
            continue
        if any(k.startswith(p) for p in BANNED_PREFIXES):
            found.append(k)
    return found


def test_scrub_environment_drops_known_creds() -> None:
    polluted = {
        "PATH": "/usr/bin",
        "HOME": "/home/test",
        "ANTHROPIC_API_KEY": "sk-do-not-leak",
        "OPENAI_API_KEY": "sk-also-do-not-leak",
        "AWS_ACCESS_KEY_ID": "AKIA-nope",
        "GH_TOKEN": "ghp_nope",
        "_NT_SYMBOL_PATH": "C:\\nope",
        "MY_HARMLESS": "ok",
    }
    out = scrub_environment(base=polluted)
    found = _contains_banned(out)
    assert not found, f"banned vars leaked: {found}"
    assert "PATH" in out
    assert "HOME" in out
    # Default policy drops everything that is not explicitly preserved.
    assert "MY_HARMLESS" not in out


def test_scrub_environment_honors_extra_keep_and_drop() -> None:
    polluted = {
        "PATH": "/usr/bin",
        "ACME_INTERNAL": "keep-me",
        "ACME_SECRET": "drop-me",
        "MISC_SECRET": "drop-me-too",
    }
    out = scrub_environment(
        base=polluted,
        extra_keep=("ACME_INTERNAL",),
        extra_drop=("ACME_*", "MISC_SECRET"),
    )
    assert out.get("ACME_INTERNAL") == "keep-me"
    assert "ACME_SECRET" not in out
    assert "MISC_SECRET" not in out


def test_scrub_environment_injects_new_values() -> None:
    out = scrub_environment(
        base={"PATH": "/x"},
        add={"CARDS_RUNNER_CARD_PATH": "/some/card.md"},
    )
    assert out["CARDS_RUNNER_CARD_PATH"] == "/some/card.md"


@pytest.mark.timeout(30)
def test_end_to_end_no_creds_leak_to_subprocess(tmp_path: Path) -> None:
    """Spawn a tiny sentinel worker via the same scrubbed env path the
    daemon uses and assert no banned vars reached the child.

    Skipped on platforms where we cannot construct the Job Object
    (we still spawn via plain Popen on POSIX with a clean env).
    """
    # Pollute the env that would have been inherited.
    polluted = {
        **os.environ,
        "ANTHROPIC_API_KEY": "sk-secret",
        "OPENAI_API_KEY": "sk-secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "GH_TOKEN": "ghp_test",
        "_NT_SYMBOL_PATH": "C:\\symbols",
    }
    dump_target = tmp_path / "env_dump.json"
    code = textwrap.dedent(
        f"""\
        import json, os, sys
        out_path = r"{dump_target}"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(dict(os.environ), f)
        sys.exit(0)
        """
    )
    sentinel_script = tmp_path / "sentinel.py"
    sentinel_script.write_text(code, encoding="utf-8")

    env = scrub_environment(
        base=polluted,
        add={
            "CARDS_RUNNER_SENTINEL": "1",
            "PYTHONUNBUFFERED": "1",
        },
    )
    rc = subprocess.run(
        [sys.executable, str(sentinel_script)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    ).returncode
    assert rc == 0, "sentinel script failed"
    dumped: dict[str, Any] = json.loads(dump_target.read_text(encoding="utf-8"))
    leaked = _contains_banned(dumped)
    assert not leaked, (
        f"banned variables reached the worker subprocess: {leaked}; "
        f"this is a contract violation (RUNNER_CONTRACT.md sec "
        f"'Worktree isolation and cross-contamination defense')"
    )
    # The injected variable survives.
    assert dumped.get("CARDS_RUNNER_SENTINEL") == "1"
