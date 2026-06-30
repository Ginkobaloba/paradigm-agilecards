"""Logging setup for daemon and worker.

Two destinations:

- stderr at INFO (for foreground operation).
- A rolling file under `<todo_root>/_runs/daemon.log` for the daemon
  and `<todo_root>/_runs/<attempt_trace_id>/worker.log` for workers.

Chunk 1 keeps formatting simple (one line per event, ISO 8601 UTC
prefix). The structured event stream lands in chunk 4.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_daemon_logging(log_dir: Path | None, *, verbose: bool = False) -> None:
    """Configure the root logger for daemon mode.

    Idempotent: calling twice replaces the handlers.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(stderr_handler)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "daemon.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(file_handler)


def setup_worker_logging(log_dir: Path, *, verbose: bool = False) -> None:
    """Configure logging for a worker subprocess.

    Writes to `worker.log` under the attempt's run dir. Stderr stays
    quiet by default so the daemon's captured stderr does not get
    spammed with per-step lines; the worker.log file is the canonical
    place to look for what the worker was doing.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "worker.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(stderr_handler)
