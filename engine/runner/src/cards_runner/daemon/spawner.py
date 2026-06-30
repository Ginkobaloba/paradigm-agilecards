"""Worker subprocess spawn.

Builds the scrubbed env, wraps the worker process in a Windows Job
Object (or a POSIX process group), and hands the daemon a
`ManagedProcess` it can kill cleanly.

The worker module is always `cards_runner.worker_stub`; which
executor it runs (stub vs the chunk 2b-ii `SdkInvoker`) is decided
inside the worker from the `CARDS_RUNNER_INVOKER` env var the spawner
injects. The spawn surface itself does not change.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import cards_runner
from ..common.env_scrub import scrub_environment
from ..common.process_group import ManagedProcess, spawn_in_job
from ..common.types import ClaimedCard, DaemonConfig


log = logging.getLogger(__name__)


def _package_src_dir() -> Path:
    """Return the `src/` dir that holds the `cards_runner` package.

    Used to seed PYTHONPATH for spawned workers when the runner is
    not pip-installed (`pip install -e .`). When the runner is
    installed, the import still resolves from `cards_runner.__file__`
    and the PYTHONPATH entry is harmless.
    """
    pkg_init = Path(cards_runner.__file__).resolve()
    # src/cards_runner/__init__.py -> src/
    return pkg_init.parent.parent


def spawn_worker(
    *,
    cfg: DaemonConfig,
    claim: ClaimedCard,
    run_dir: Path,
    extra_env: dict[str, str] | None = None,
    extra_keep: tuple[str, ...] = (),
    extra_drop: tuple[str, ...] = (),
) -> ManagedProcess:
    """Spawn the per-card worker for `claim`.

    The worker is `python -m cards_runner.worker_stub`. It reads the
    projected card file the daemon wrote into the run dir; the card
    path, worktree path, and trace ids are injected via env vars.

    Env block is scrubbed via `common.env_scrub.scrub_environment`.
    Tests assert no `ANTHROPIC_*`, `OPENAI_*`, `AWS_*`, `GH_TOKEN`,
    or `_NT_SYMBOL_PATH` reach the worker process; see
    `tests/test_env_scrub.py`.
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    # Seed PYTHONPATH so the worker can import `cards_runner` even
    # when the package is not pip-installed. The scrub policy drops
    # PYTHONPATH from inheritance by default, so we inject the path
    # we resolved from our own import location.
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_entries = [str(_package_src_dir())]
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    pythonpath = os.pathsep.join(pythonpath_entries)

    injected: dict[str, str] = {
        "CARDS_RUNNER_CARD_PATH": str(claim.card_file),
        "CARDS_RUNNER_WORKTREE": str(claim.worktree_path),
        "CARDS_RUNNER_ATTEMPT_TRACE_ID": claim.attempt_trace_id,
        "CARDS_RUNNER_TRACE_ID": claim.trace_id,
        "CARDS_RUNNER_HEARTBEAT_INTERVAL_SEC": str(cfg.heartbeat_interval_sec),
        "CARDS_RUNNER_STUB_SLEEP_SEC": str(cfg.stub_sleep_sec),
        "CARDS_RUNNER_RUN_DIR": str(run_dir),
        "CARDS_RUNNER_INVOKER": cfg.invoker,
        "PYTHONPATH": pythonpath,
    }
    if cfg.invoker == "sdk":
        # The SDK executor needs a credential the scrub policy drops
        # by default. RUNNER_CONTRACT.md "Worktree isolation" item 1
        # forbids leaking `ANTHROPIC_*` UNLESS the project explicitly
        # opts in; running `--invoker sdk` IS that explicit opt-in, so
        # the daemon injects exactly one key (not the whole prefix)
        # through `scrub_environment(add=...)`.
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            injected["ANTHROPIC_API_KEY"] = api_key
        else:
            log.error(
                "invoker=sdk but ANTHROPIC_API_KEY is not set in the "
                "daemon environment; the SDK worker will fail to start"
            )
        # Optional executor knobs: pass through only when set, so the
        # worker's `SdkInvoker.from_env` defaults otherwise apply.
        for passthrough in (
            "CARDS_RUNNER_CASCADE_THRESHOLD",
            "CARDS_RUNNER_MAX_OUTPUT_TOKENS",
            "CARDS_RUNNER_MAX_ESCALATIONS",
            "CARDS_RUNNER_PRICING_JSON",
            # Chunk 3: flip the SdkInvoker into the tool-using
            # multi-turn loop when set. The CLI's `--invoker sdk-tools`
            # sets this in the daemon environment.
            "CARDS_RUNNER_USE_TOOLS",
            "CARDS_RUNNER_MAX_TOOL_TURNS",
        ):
            value = os.environ.get(passthrough)
            if value:
                injected[passthrough] = value
    if extra_env:
        injected.update(extra_env)

    env = scrub_environment(
        extra_drop=extra_drop,
        extra_keep=extra_keep,
        add=injected,
    )

    args = [
        sys.executable,
        "-m",
        "cards_runner.worker_stub",
    ]

    log.info(
        "spawning %s worker card_id=%s attempt=%s pid_parent=%d",
        cfg.invoker, claim.card_id, claim.attempt_trace_id, os.getpid(),
    )

    log_path = run_dir / "worker.stdout.log"
    err_path = run_dir / "worker.stderr.log"
    stdout = open(log_path, "ab", buffering=0)
    stderr = open(err_path, "ab", buffering=0)
    try:
        process = spawn_in_job(
            args,
            cwd=str(claim.worktree_path),
            env=env,
            stdout=stdout.fileno(),
            stderr=stderr.fileno(),
        )
    finally:
        # The duplicated handles inside the child remain valid even
        # after we close ours.
        stdout.close()
        stderr.close()
    # Drop a worker.pid file the boot-time alive check (chunk 4) and any
    # external troubleshooter can consult. The file's presence is not
    # load-bearing; the alive check tolerates a missing pid file by
    # falling back to the heartbeat-age heuristic.
    try:
        pid = getattr(process, "pid", None)
        if pid is not None:
            (run_dir / "worker.pid").write_text(str(int(pid)), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write worker.pid for %s: %s", claim.card_id, exc)
    return process
