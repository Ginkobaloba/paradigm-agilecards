"""`python -m cards_runner.daemon` entry point.

Same as `cards-runner start`. Kept here so the daemon can be invoked
directly from Windows service wrappers without depending on the
console_scripts shim.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ..common.types import DaemonConfig
from .daemon import Daemon


def main() -> int:
    parser = argparse.ArgumentParser(prog="cards-runner-daemon")
    parser.add_argument("--todo-root", type=Path, required=True)
    parser.add_argument("--poll-interval-sec", type=float, default=5.0)
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--orphan-timeout-minutes", type=int, default=120)
    parser.add_argument("--heartbeat-interval-sec", type=float, default=30.0)
    parser.add_argument("--stub-sleep-sec", type=float, default=3.0)
    parser.add_argument("--skip-worktree", action="store_true")
    args = parser.parse_args()
    cfg = DaemonConfig(
        todo_root=args.todo_root,
        poll_interval_sec=args.poll_interval_sec,
        max_parallel=args.max_parallel,
        orphan_timeout_minutes=args.orphan_timeout_minutes,
        heartbeat_interval_sec=args.heartbeat_interval_sec,
        stub_sleep_sec=args.stub_sleep_sec,
        skip_worktree=args.skip_worktree,
    )
    return Daemon(cfg).run()


if __name__ == "__main__":
    raise SystemExit(main())
