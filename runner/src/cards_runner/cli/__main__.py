"""`cards-runner` CLI.

Surfaces the minimal subset the chunk asks for:

- `start`   boot the daemon (foreground)
- `stop`    signal the daemon to drain and exit
- `status`  print daemon state plus per-status card counts
- `reclaim` force-reclaim a specific `active` card back to `backlog`

Chunks 3-4 will add `verify`, `approve`, `pause`, `resume`, `doctor`,
and `pricing reload`. After the chunk 2b cutover `status` and
`reclaim` read the card store, not a filesystem tree.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from ..common.locks import FileLock, pid_alive
from ..common.types import DaemonConfig, RuntimePaths
from ..daemon.daemon import Daemon, DaemonAlreadyRunning
from ..daemon.orphan import force_reclaim
from ..store import CardStatus, build_repository, default_store_spec
from ..store.repository import CardRepository

# The card statuses `status` reports, in display order.
_STATUS_ORDER: tuple[str, ...] = (
    CardStatus.BACKLOG.value,
    CardStatus.ACTIVE.value,
    CardStatus.AMENDMENTS.value,
    CardStatus.AWAITING_STANDUP_REVIEW.value,
    CardStatus.DONE.value,
    CardStatus.BLOCKED.value,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cards-runner",
        description="agile-cards runner CLI (chunk 2b)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="boot the daemon (foreground)")
    _add_common(p_start)
    p_start.add_argument("--poll-interval-sec", type=float, default=5.0)
    p_start.add_argument("--max-parallel", type=int, default=4)
    p_start.add_argument("--orphan-timeout-minutes", type=int, default=120)
    p_start.add_argument("--heartbeat-interval-sec", type=float, default=30.0)
    p_start.add_argument("--stub-sleep-sec", type=float, default=3.0)
    p_start.add_argument(
        "--invoker",
        choices=("stub", "sdk", "sdk-tools"),
        default=os.environ.get("CARDS_RUNNER_INVOKER", "stub"),
        help="executor to run per card: 'stub' (default, zero tokens), "
        "'sdk' (reasoning-only Anthropic-SDK executor), or 'sdk-tools' "
        "(chunk 3 tool-using executor with file/shell/git tools; needs "
        "ANTHROPIC_API_KEY in the daemon environment)",
    )
    p_start.add_argument(
        "--skip-worktree",
        action="store_true",
        help="skip git worktree creation (for tests against non-git roots)",
    )
    p_start.add_argument(
        "--no-verifier",
        action="store_true",
        help="disable the cold-read verifier; a clean executor exit "
        "leaves the card active (chunk 2 baseline behavior)",
    )

    # Chunk-4 merge-gate flags (added to the CLI in chunk 5).
    p_start.add_argument(
        "--pr-gate",
        action="store_true",
        help="enable the tier-aware merge gate: route verifier-pass "
        "cards through `gh pr create` (and `gh pr merge --auto` for "
        "tier 1-2). Off by default; chunk-3 behavior leaves cards in "
        "`done` after a verifier pass.",
    )
    p_start.add_argument(
        "--gh", dest="gh_path", default=None,
        help="path to the gh binary (default: `gh` on PATH)",
    )
    p_start.add_argument(
        "--git", dest="git_path", default=None,
        help="path to the git binary (default: `git` on PATH)",
    )
    p_start.add_argument(
        "--auto-merge-strategy",
        choices=("squash", "merge", "rebase"),
        default=None,
        help="auto-merge strategy passed to `gh pr merge` (default squash)",
    )
    p_start.add_argument(
        "--pr-base",
        dest="pr_base_branch_default",
        default=None,
        help="default PR base branch (overridden per-card by "
        "frontmatter.base_branch or by project.yaml; default `main`)",
    )
    p_start.add_argument(
        "--no-boot-alive-check", action="store_true",
        help="skip the boot-time worker-alive check; fall back to the "
        "orphan-timeout window for reclaiming dead-worker active cards",
    )
    p_start.add_argument(
        "--forensic-ttl-hours", type=int, default=None,
        help="forensic run-dir TTL in hours (default 24). 0 disables "
        "the reaper entirely.",
    )

    # Chunk-5 unblocker + reviewer flags.
    p_start.add_argument(
        "--pr-unblock", action="store_true",
        help="poll `gh pr view` once per tick for blocked-on-merge cards "
        "and promote them to `done` when the PR reports MERGED. Off by "
        "default; production runs typically set this alongside --pr-gate.",
    )
    p_start.add_argument(
        "--sibling-reviewer", action="store_true",
        help="run the sibling-agent reviewer for tier-3/4 PRs each tick. "
        "Reads the PR diff + card body, posts `gh pr review`, and (on "
        "approve) fires `gh pr merge --auto`. Off by default; requires "
        "the project's project.yaml to also enable it.",
    )
    p_start.add_argument(
        "--amendment-reviewer", action="store_true",
        help="run the AC-amendment reviewer each tick. Walks "
        "`amendments` cards, decides approve/deny/comment via the "
        "configured reviewer client, and routes accordingly. Off by "
        "default; the project's project.yaml must also enable it.",
    )
    p_start.add_argument(
        "--worktree-prune", action="store_true",
        help="enable hourly `git worktree prune` sweeps against each "
        "project the runner touches. Off by default.",
    )
    p_start.add_argument(
        "--worktree-prune-interval-sec", type=int, default=None,
        help="how often to run the worktree prune sweep, in seconds "
        "(default 3600). Ignored when --worktree-prune is off.",
    )
    p_start.add_argument(
        "--project-config",
        dest="project_config_path", type=Path, default=None,
        help="path to a project.yaml override (default "
        "`<todo-root>/project.yaml`). Missing file is OK; defaults apply.",
    )

    p_stop = sub.add_parser("stop", help="signal the daemon to drain and exit")
    _add_common(p_stop)
    p_stop.add_argument("--timeout-sec", type=float, default=60.0)

    p_status = sub.add_parser("status", help="print daemon state")
    _add_common(p_status)
    p_status.add_argument("--json", action="store_true")

    p_reclaim = sub.add_parser(
        "reclaim", help="force-reclaim a card from active to backlog"
    )
    _add_common(p_reclaim)
    p_reclaim.add_argument("card_id")
    p_reclaim.add_argument(
        "--force",
        action="store_true",
        help="skip the interactive confirmation",
    )

    args = parser.parse_args(argv)
    if args.cmd == "start":
        return _cmd_start(args)
    if args.cmd == "stop":
        return _cmd_stop(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "reclaim":
        return _cmd_reclaim(args)
    parser.error(f"unknown subcommand {args.cmd}")
    return 2  # unreachable


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--todo-root",
        type=Path,
        default=Path(os.environ.get("CARDS_TODO_ROOT", r"C:\dev\todo")),
    )
    p.add_argument(
        "--store",
        default=os.environ.get("CARDS_STORE", ""),
        help="card store spec (sqlite:PATH or dolt:DIR); "
        "default is sqlite:<todo-root>/cards.db",
    )


def _resolve_store(args: argparse.Namespace) -> str:
    return args.store or default_store_spec(args.todo_root)


def _open_store(args: argparse.Namespace) -> CardRepository:
    """Open and schema-initialize the card store for a CLI command."""
    repo = build_repository(_resolve_store(args))
    repo.initialize_schema()
    return repo


def _cmd_start(args: argparse.Namespace) -> int:
    cfg_kwargs: dict[str, Any] = dict(
        todo_root=args.todo_root,
        store_spec=args.store,
        poll_interval_sec=args.poll_interval_sec,
        max_parallel=args.max_parallel,
        orphan_timeout_minutes=args.orphan_timeout_minutes,
        heartbeat_interval_sec=args.heartbeat_interval_sec,
        stub_sleep_sec=args.stub_sleep_sec,
        invoker=args.invoker,
        skip_worktree=args.skip_worktree,
        verifier_enabled=not getattr(args, "no_verifier", False),
        pr_gate_enabled=bool(getattr(args, "pr_gate", False)),
        pr_unblock_enabled=bool(getattr(args, "pr_unblock", False)),
        sibling_reviewer_enabled=bool(
            getattr(args, "sibling_reviewer", False)
        ),
        amendment_reviewer_enabled=bool(
            getattr(args, "amendment_reviewer", False)
        ),
        worktree_prune_enabled=bool(getattr(args, "worktree_prune", False)),
        boot_worker_alive_check=not bool(
            getattr(args, "no_boot_alive_check", False)
        ),
    )
    # Optional overrides; only thread them through when the user passed
    # one, otherwise the DaemonConfig default applies.
    for cli_name, cfg_name in (
        ("gh_path", "gh_path"),
        ("git_path", "git_path"),
        ("auto_merge_strategy", "auto_merge_strategy"),
        ("pr_base_branch_default", "pr_base_branch_default"),
        ("forensic_ttl_hours", "worktree_forensic_ttl_hours"),
        ("worktree_prune_interval_sec", "worktree_prune_interval_sec"),
        ("project_config_path", "project_config_path"),
    ):
        value = getattr(args, cli_name, None)
        if value is not None:
            cfg_kwargs[cfg_name] = value
    cfg = DaemonConfig(**cfg_kwargs)
    if args.invoker in ("sdk", "sdk-tools") and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: --invoker sdk/sdk-tools needs ANTHROPIC_API_KEY in "
            "the environment",
            file=sys.stderr,
        )
        return 2
    # The `sdk-tools` choice is exposed as a separate CLI value for
    # discoverability; the daemon's invoker field still uses `sdk` and
    # the worker is flipped into tools mode by the CARDS_RUNNER_USE_TOOLS
    # env var the spawner now passes through.
    if args.invoker == "sdk-tools":
        os.environ["CARDS_RUNNER_USE_TOOLS"] = "1"
        # The DaemonConfig's invoker keeps the canonical "sdk" name so
        # spawner.py's existing "if cfg.invoker == 'sdk'" branch keeps
        # working; the env var flips the executor into tool-using mode.
        cfg = DaemonConfig(**{**cfg.__dict__, "invoker": "sdk"})
    try:
        return Daemon(cfg).run()
    except DaemonAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _cmd_stop(args: argparse.Namespace) -> int:
    paths = RuntimePaths.from_root(args.todo_root)
    lock = FileLock(paths.daemon_lock)
    pid = lock.read_pid()
    if pid is None:
        print("daemon not running (no lockfile PID)", file=sys.stderr)
        return 2
    if not pid_alive(pid):
        print(
            f"daemon lockfile holds pid={pid} but the process is gone",
            file=sys.stderr,
        )
        return 2
    try:
        if sys.platform == "win32":
            # On Windows os.kill with signal.SIGTERM raises; use
            # CTRL_BREAK_EVENT against the daemon's process group.
            os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"failed to signal daemon pid={pid}: {exc}", file=sys.stderr)
        return 1
    print(f"sent stop signal to daemon pid={pid}; waiting...")
    deadline = time.monotonic() + args.timeout_sec
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            print("daemon exited")
            return 0
        time.sleep(0.5)
    print("daemon still running after timeout", file=sys.stderr)
    return 1


def _cmd_status(args: argparse.Namespace) -> int:
    paths = RuntimePaths.from_root(args.todo_root)
    lock = FileLock(paths.daemon_lock)
    pid = lock.read_pid()
    running = pid is not None and pid_alive(pid)
    store_spec = _resolve_store(args)
    repo = _open_store(args)
    try:
        counts = {
            status: len(repo.query_cards(status=status))
            for status in _STATUS_ORDER
        }
        total = repo.count_cards()
    finally:
        repo.close()
    payload: dict[str, Any] = {
        "todo_root": str(paths.todo_root),
        "store": store_spec,
        "daemon_pid": pid,
        "daemon_running": running,
        "card_total": total,
        "counts": counts,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"todo_root: {payload['todo_root']}")
    print(f"store: {store_spec}")
    print(
        f"daemon: {'running' if running else 'stopped'} "
        f"(pid={pid if pid else 'none'})"
    )
    print(f"cards: {total} total")
    print(
        "counts: "
        + " ".join(f"{status}={counts[status]}" for status in _STATUS_ORDER)
    )
    return 0


def _cmd_reclaim(args: argparse.Namespace) -> int:
    if not args.force:
        ans = input(f"reclaim {args.card_id} from active -> backlog? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("aborted")
            return 0
    repo = _open_store(args)
    try:
        record = force_reclaim(repo, args.card_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        repo.close()
    print(f"reclaimed: {record.card_id} (status={record.status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
