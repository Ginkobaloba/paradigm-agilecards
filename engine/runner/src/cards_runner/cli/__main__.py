"""`cards-runner` CLI.

Surfaces:

- `start`   boot the daemon (foreground)
- `stop`    signal the daemon to drain and exit
- `status`  print daemon state plus per-status card counts
- `reclaim` force-reclaim a specific `active` card back to `backlog`
- `doctor`  diagnostic dump: resolved binaries, project config,
            schema migration status, knob settings (chunk 6c)

Chunks 7+ may add `verify`, `approve`, `pause`, `resume`, and
`pricing reload`. After the chunk 2b cutover `status` and `reclaim`
read the card store, not a filesystem tree.
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

from ..common.canonical_config import ENV_TIER_MAP_PATH, CanonicalConfigMissing, load_tier_map
from ..common.envfile import ENV_FILE_VAR, load_dotenv
from ..common.locks import FileLock, pid_alive
from ..common import git_safety
from ..common.types import DaemonConfig, RuntimePaths
from ..daemon import llm_capability
from ..daemon.daemon import Daemon, DaemonAlreadyRunning
from ..daemon.orphan import force_reclaim
from ..providers.registry import (
    KEYLESS_LOCAL_PROVIDERS,
    LOCAL_BASE_URL_ENV,
    base_url_problem,
    tiers_needing_a_key,
)
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
    # Entry point only; never overrides a variable already set. The key used
    # to come from a User-scope variable, removed 2026-10-21.
    load_dotenv()
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
        "--on-local-map-unavailable", choices=("stop", "warn"), default="stop",
        help="when the pinned local tier map (CARDS_RUNNER_TIER_MAP_YAML) is missing "
        "or broken: 'stop' (default) fails closed; 'warn' runs the card on the "
        "hosted (PAID) map if ANTHROPIC_API_KEY was set at start, warning at start "
        "and per card, naming the model on the card, and logging an event",
    )
    p_start.add_argument(
        "--worktree-prune", action="store_true",
        help="obsolete and ignored (workers use throwaway clones, not git "
        "worktrees); accepted so existing launch commands still start.",
    )
    p_start.add_argument(
        "--worktree-prune-interval-sec", type=int, default=None,
        help="obsolete and ignored, with --worktree-prune.",
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

    p_trust = sub.add_parser(
        "trust-repo",
        help="record a repository's current git config and hooks as trusted "
        "(also lifts a quarantine); inspect .git/config and .git/hooks first",
    )
    _add_common(p_trust)
    p_trust.add_argument("project_dir", type=Path)

    p_doctor = sub.add_parser(
        "doctor",
        help="diagnostic dump: resolved binaries, project config, "
        "schema migration status, knob settings",
    )
    _add_common(p_doctor)
    p_doctor.add_argument(
        "--json", action="store_true",
        help="JSON output (default: human-readable text)",
    )
    p_doctor.add_argument(
        "--skip-store", action="store_true",
        help="skip the card-store schema introspection (does not open "
        "the store; useful when the store is locked by a running daemon)",
    )

    p_stats = sub.add_parser(
        "stats",
        help="throughput-metrics read APIs (ledger chunk 3+)",
    )
    stats_sub = p_stats.add_subparsers(dest="stats_cmd", required=True)
    p_recalibrate = stats_sub.add_parser(
        "recalibrate",
        help="recompute `metric_estimates` from `card_metrics`",
    )
    _add_common(p_recalibrate)
    p_recalibrate.add_argument(
        "--tenant", default="default",
        help="tenant scope to recalibrate (default: 'default')",
    )
    p_recalibrate.add_argument(
        "--priors", type=Path, default=None,
        help="path to a custom priors YAML; defaults to the in-tree "
        "runner/templates/metrics_priors.yaml",
    )
    p_recalibrate.add_argument(
        "--floor-n", type=int, default=10,
        help="bucket sample count below which the layered prior "
        "falls through to tier-aggregate (default: 10)",
    )
    p_recalibrate.add_argument(
        "--json", action="store_true",
        help="JSON output (default: one line per bucket)",
    )

    p_calibration = stats_sub.add_parser(
        "calibration",
        help="per-bucket confidence-band regression table from "
        "gate shadow decisions (gate chunk 3)",
    )
    _add_common(p_calibration)
    p_calibration.add_argument(
        "--tenant", default="default",
        help="tenant scope (default: 'default')",
    )
    p_calibration.add_argument(
        "--work-type", default=None,
        help="bucket work_type; omit to report every bucket present "
        "in the shadow log",
    )
    p_calibration.add_argument(
        "--tier", type=int, default=None,
        help="bucket tier; required when --work-type is given",
    )
    p_calibration.add_argument(
        "--window", type=int, default=100,
        help="most-recent-N-cards window (default: 100)",
    )
    p_calibration.add_argument(
        "--bands", type=int, default=10,
        help="number of confidence bands (default: 10 deciles)",
    )
    p_calibration.add_argument(
        "--json", action="store_true",
        help="JSON output (default: fixed-width table)",
    )

    p_ramp = stats_sub.add_parser(
        "ramp",
        help="confidence-gate ramp phases: show state, advance a "
        "bucket (operator-explicit, gate chunk 3)",
    )
    ramp_sub = p_ramp.add_subparsers(dest="ramp_cmd", required=True)
    p_ramp_show = ramp_sub.add_parser(
        "show", help="per-bucket phase, alarm, and advancement readiness"
    )
    _add_common(p_ramp_show)
    p_ramp_show.add_argument("--tenant", default="default")
    p_ramp_show.add_argument("--json", action="store_true")
    p_ramp_advance = ramp_sub.add_parser(
        "advance",
        help="advance one bucket's phase after the section 9.3 gates "
        "pass; refuses otherwise",
    )
    _add_common(p_ramp_advance)
    p_ramp_advance.add_argument("--tenant", default="default")
    p_ramp_advance.add_argument(
        "--bucket", required=True,
        help="bucket as work_type:tier, e.g. feature:3",
    )
    p_ramp_advance.add_argument(
        "--confirm", action="store_true",
        help="actually apply the advancement; without it the gates "
        "are evaluated and printed but nothing changes",
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
    if args.cmd == "trust-repo":
        return _cmd_trust_repo(args)
    if args.cmd == "doctor":
        return _cmd_doctor(args)
    if args.cmd == "stats":
        if args.stats_cmd == "recalibrate":
            return _cmd_stats_recalibrate(args)
        if args.stats_cmd == "calibration":
            return _cmd_stats_calibration(args)
        if args.stats_cmd == "ramp":
            if args.ramp_cmd == "show":
                return _cmd_stats_ramp_show(args)
            if args.ramp_cmd == "advance":
                return _cmd_stats_ramp_advance(args)
            parser.error(f"unknown ramp subcommand {args.ramp_cmd}")
        parser.error(f"unknown stats subcommand {args.stats_cmd}")
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


def _anthropic_key_problem(args: argparse.Namespace) -> str | None:
    """Why this `start` needs ANTHROPIC_API_KEY and lacks it, or None.

    The key may be absent only when nothing would call Anthropic: the
    reasoning-only `sdk` executor, no Claude reviewer, and every tier of
    the map the WORKER will load resolving to a non-Anthropic provider
    (the worker calls `load_tier_map()` with the same env, which the
    spawner passes through). `sdk-tools` always needs it: the tool loop
    is Anthropic-only, as the OpenAI-compatible adapter has no tool turns.
    """
    if args.invoker not in ("sdk", "sdk-tools") or os.environ.get("ANTHROPIC_API_KEY"):
        return None
    if args.invoker == "sdk-tools":
        return "--invoker sdk-tools needs ANTHROPIC_API_KEY (the tool loop is Anthropic-only)"
    for flag in ("sibling_reviewer", "amendment_reviewer"):
        if getattr(args, flag, False):
            return f"--{flag.replace('_', '-')} uses a Claude reviewer and needs ANTHROPIC_API_KEY"
    tier_map = load_tier_map()
    hosted = tiers_needing_a_key(tier_map.points_to_model_map())
    if hosted:
        named = ", ".join(
            f"tier {points} ({model}, provider {provider})" for points, model, provider in hosted
        )
        return (
            f"--invoker sdk without ANTHROPIC_API_KEY runs only keyless local "
            f"providers ({', '.join(sorted(KEYLESS_LOCAL_PROVIDERS))}): {named} "
            f"in {tier_map.source} is not one"
        )
    return None


def _local_map_unavailable() -> str | None:
    """Why the PINNED tier map cannot load (missing or broken), or None.

    This is the one condition `--on-local-map-unavailable` governs. A SET
    env var is a pin (canonical_config.load_tier_map): it never silently
    falls back to another map, so under `stop` this refuses the start.
    """
    raw = os.environ.get(ENV_TIER_MAP_PATH)
    if not raw:
        return None
    if not Path(raw).is_file():
        return f"{ENV_TIER_MAP_PATH} points at {raw}, which is not a file"
    try:
        load_tier_map()
    except CanonicalConfigMissing as exc:
        return f"{ENV_TIER_MAP_PATH}: {exc}"
    return None


def _paid_fallback_banner(why: str | None) -> str:
    """The start-time warning for `--on-local-map-unavailable warn`."""
    hosted = load_tier_map(honor_pin=False)
    paid = ", ".join(f"tier {p}: {m}" for p, m, _ in tiers_needing_a_key(hosted.points_to_model_map()))
    rule = "!" * 72
    if why is not None:
        head = ("WARNING: the local tier map is UNAVAILABLE; cards will run on PAID "
                f"hosted models ({why}).")
    else:
        head = ("NOTICE: --on-local-map-unavailable warn. If the local tier map becomes "
                "unavailable, cards will run on PAID hosted models.")
    return f"{rule}\n{head}\nHosted map {hosted.source}: {paid or 'no paid tier'}\n{rule}"


def _tier_map_problem() -> str | None:
    """Why the tier map the worker will load is unusable, or None.

    Checked once at start so a bad map is a clean refusal naming the
    problem, not a traceback here or a failure on the first card. A SET
    env var is a pin (canonical_config.load_tier_map), so broken YAML
    raises rather than falling back to another map.
    """
    try:
        tier_map = load_tier_map()
    except CanonicalConfigMissing as exc:
        return f"{ENV_TIER_MAP_PATH}: {exc}"
    for points, entry in sorted(tier_map.tiers.items()):
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip():
            return f"tier {points} in {tier_map.source} has no `model`"
    return None


def _pin_worker_env() -> str | None:
    """Make the env the worker inherits mean what the CLI checked, or say why not.

    The worker runs in the card's worktree, not the CLI's cwd, so a
    RELATIVE `CARDS_RUNNER_TIER_MAP_YAML` would resolve somewhere else
    there, miss, and fall through to the repo-root Claude map: the CLI
    approves a local map while the worker bills Claude. So it is made
    absolute here, before the key check reads it, and the spawner passes
    the absolute path on. A path that names no file is refused for the
    same reason. Base URLs are forwarded past the env scrub, so one that
    carries a credential is refused (the URL itself is not echoed).
    """
    raw = os.environ.get(ENV_TIER_MAP_PATH)
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        # Absolute even when missing: a map that reappears later is then
        # the same file for the worker. Whether a missing one may start is
        # `_local_map_unavailable`'s question.
        os.environ[ENV_TIER_MAP_PATH] = str(path)
    for name in LOCAL_BASE_URL_ENV:
        url = os.environ.get(name)
        if url:
            why = base_url_problem(url)
            if why:
                return f"{name} {why}; it reaches the worker, so it must be an endpoint only"
    return None


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
        on_local_map_unavailable=getattr(args, "on_local_map_unavailable", "stop"),
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
    # The CLI is the ONLY tools switch, set before anything reads it: an
    # ambient CARDS_RUNNER_USE_TOOLS=1 would otherwise put a `--invoker sdk`
    # worker in tools mode and slip past the refusal below.
    if args.invoker == "sdk-tools":
        os.environ["CARDS_RUNNER_USE_TOOLS"] = "1"
    else:
        os.environ.pop("CARDS_RUNNER_USE_TOOLS", None)
    tools_mode = os.environ.get("CARDS_RUNNER_USE_TOOLS") == "1"
    if tools_mode and getattr(args, "pr_gate", False):
        # Tool mode lets the executor write anything in its worktree,
        # including git config and hooks; the PR gate then runs `git push`
        # and `gh` there with real credentials. common/git_safety.py
        # hardens that path, but same-user processes are not isolated on
        # Windows, so the pair stays refused until a deliberate later
        # change lifts it (Drew's call, with tool-shell network policy).
        print(
            "error: --invoker sdk-tools with --pr-gate is refused: the "
            "merge gate would run git and gh with credentials in a "
            "worktree the executor could have rigged",
            file=sys.stderr,
        )
        return 2
    if args.invoker in ("sdk", "sdk-tools"):
        env_problem = _pin_worker_env()
        if env_problem is not None:
            print(f"error: {env_problem}", file=sys.stderr)
            return 2
        unavailable = _local_map_unavailable()
        warn = getattr(args, "on_local_map_unavailable", "stop") == "warn"
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if unavailable is not None and not (warn and has_key):
            hint = ("; --on-local-map-unavailable warn falls back to the hosted map only "
                    "when ANTHROPIC_API_KEY is set" if warn else "")
            print(f"error: {unavailable}{hint}", file=sys.stderr)
            return 2
        if warn and has_key and os.environ.get(ENV_TIER_MAP_PATH):
            print(_paid_fallback_banner(unavailable), file=sys.stderr)
        if unavailable is None:
            map_problem = _tier_map_problem()
            if map_problem is not None:
                print(f"error: {map_problem}", file=sys.stderr)
                return 2
    key_problem = _anthropic_key_problem(args)
    if key_problem is not None:
        print(
            f"error: {key_problem}; set ANTHROPIC_API_KEY in "
            "the environment (or in a gitignored .env in the working "
            f"directory, or the file named by {ENV_FILE_VAR})",
            file=sys.stderr,
        )
        return 2
    # The `sdk-tools` choice is exposed as a separate CLI value for
    # discoverability; the daemon's invoker field still uses `sdk` and
    # the worker is flipped into tools mode by the CARDS_RUNNER_USE_TOOLS
    # env var the spawner now passes through.
    if args.invoker == "sdk-tools":
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
    # The DAEMON's capability, recorded by the daemon at boot. This process's
    # own environment says nothing about the daemon's, so it is not consulted.
    record = llm_capability.read_for_daemon(
        paths.capabilities, pid if running else None
    )
    llm_reviews = record["llm_reviews"] if record else "unknown"
    payload: dict[str, Any] = {
        "todo_root": str(paths.todo_root),
        "store": store_spec,
        "daemon_pid": pid,
        "daemon_running": running,
        "card_total": total,
        "counts": counts,
        "llm_reviews": llm_reviews,
        "llm_reviews_reason": record.get("reason") if record else None,
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
    if llm_reviews == "degraded":
        print(f"llm reviews: DEGRADED ({payload['llm_reviews_reason']})")
    elif llm_reviews == "live":
        print("llm reviews: live")
    else:
        print("llm reviews: unknown (no record from the running daemon)")
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


def _cmd_trust_repo(args: argparse.Namespace) -> int:
    """`cards-runner trust-repo <project>`: (re)record the git baseline.

    The baseline is only as good as the repo is right now, which is why
    this is a human step: look at .git/config and .git/hooks first.
    """
    git_safety.configure(git_safety.state_dir_for(args.todo_root))
    common = git_safety.common_dir_of(args.project_dir.resolve())
    if common is None:
        print(f"error: {args.project_dir} is not a git checkout", file=sys.stderr)
        return 3
    previous = git_safety.trust_record(common)
    value = git_safety.trust_repo(common)
    if previous is not None and previous.quarantined:
        print(f"quarantine lifted: {common} (was: {previous.reason})")
    print(f"trusted: {common} baseline={value[:12]}")
    return 0


def _cmd_stats_recalibrate(args: argparse.Namespace) -> int:
    """`cards-runner stats recalibrate` -- refresh `metric_estimates`.

    Reads every populated `(work_type, tier)` bucket from
    `card_metrics`, computes per-bucket percentile estimates blended
    with the cold-start prior, and writes `metric_estimates`.
    Idempotent: re-running on unchanged data produces the same row.
    """
    from ..metrics import load_priors, recalibrate_all
    from ..metrics.store import MetricsStore

    priors = load_priors(args.priors)
    repo = _open_store(args)
    try:
        store = MetricsStore.from_repository(repo)
        results = recalibrate_all(
            store,
            priors,
            tenant_id=args.tenant,
            floor_n=args.floor_n,
        )
    finally:
        repo.close()

    if args.json:
        payload = [
            {
                "work_type": r.work_type,
                "tier": r.tier,
                "n_samples": r.n_samples,
                "prior_weight": round(r.prior_weight, 4),
                "written": r.written,
            }
            for r in results
        ]
        print(json.dumps({"tenant": args.tenant, "results": payload}, indent=2))
    else:
        if not results:
            print(
                f"no populated buckets for tenant '{args.tenant}'; nothing to do"
            )
        for r in results:
            print(
                f"{r.work_type}/tier{r.tier}: n={r.n_samples} "
                f"prior_weight={r.prior_weight:.3f} "
                f"({'written' if r.written else 'skipped'})"
            )
    return 0


def _calibration_payload(cal: Any) -> dict[str, Any]:
    return {
        "work_type": cal.work_type,
        "tier": cal.tier,
        "overall_n": cal.overall_n,
        "overall_regressions": cal.overall_regressions,
        "overall_regression_rate": round(cal.overall_regression_rate, 4),
        "monotonic": cal.monotonic,
        "bands": [
            {
                "lo": b.lo, "hi": b.hi, "n": b.n,
                "regressions": b.regressions,
                "regression_rate": round(b.regression_rate, 4),
            }
            for b in cal.bands if b.n > 0
        ],
    }


def _cmd_stats_calibration(args: argparse.Namespace) -> int:
    """`cards-runner stats calibration` -- the spec 8.2 banding table.

    With --work-type/--tier, one bucket; without, every bucket that has
    at least one shadow decision in the metrics event log."""
    from ..metrics.calibration import (
        buckets_in_shadow_log, calibration_for_bucket, render_table,
    )
    from ..metrics.store import MetricsStore

    if (args.work_type is None) != (args.tier is None):
        print(
            "error: --work-type and --tier must be given together",
            file=sys.stderr,
        )
        return 2
    paths = RuntimePaths.from_root(args.todo_root)
    repo = _open_store(args)
    try:
        store = MetricsStore.from_repository(repo)
        if args.work_type is not None:
            buckets = [(args.work_type, args.tier)]
        else:
            buckets = buckets_in_shadow_log(paths, tenant_id=args.tenant)
        try:
            calibrations = [
                calibration_for_bucket(
                    store, paths,
                    tenant_id=args.tenant, work_type=wt, tier=t,
                    n_bands=args.bands, window_cards=args.window,
                )
                for wt, t in buckets
            ]
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    finally:
        repo.close()

    if args.json:
        print(json.dumps({
            "tenant": args.tenant,
            "buckets": [_calibration_payload(c) for c in calibrations],
        }, indent=2))
        return 0
    if not calibrations:
        print(
            f"no gate shadow decisions for tenant '{args.tenant}'; "
            "nothing to calibrate"
        )
        return 0
    print("\n\n".join(render_table(c) for c in calibrations))
    return 0


def _parse_bucket(text: str) -> tuple[str, int]:
    work_type, sep, tier_text = text.rpartition(":")
    if not sep or not work_type:
        raise ValueError(
            f"bucket must be work_type:tier (e.g. feature:3), got {text!r}"
        )
    return work_type, int(tier_text)


def _cmd_stats_ramp_show(args: argparse.Namespace) -> int:
    """`cards-runner stats ramp show` -- phases + advancement readiness.

    Reports every bucket present in either the ramp table or the shadow
    log, so a bucket that has shadow data but no explicit phase row
    shows up at its default phase 1."""
    from ..metrics.calibration import (
        buckets_in_shadow_log, calibration_for_bucket,
        read_shadow_decisions,
    )
    from ..metrics.ramp import (
        RampStore, count_live_decisions, evaluate_advance,
        killswitch_quiet,
    )
    from ..metrics.store import MetricsStore

    paths = RuntimePaths.from_root(args.todo_root)
    repo = _open_store(args)
    try:
        store = MetricsStore.from_repository(repo)
        ramp = RampStore.from_repository(repo)
        buckets = {
            (s.work_type, s.tier)
            for s in ramp.list_states(tenant_id=args.tenant)
        } | set(buckets_in_shadow_log(paths, tenant_id=args.tenant))
        shadow = read_shadow_decisions(paths, tenant_id=args.tenant)
        ks_quiet = killswitch_quiet(paths, tenant_id=args.tenant)
        rows = []
        for work_type, tier in sorted(buckets):
            state = ramp.get(
                tenant_id=args.tenant, work_type=work_type, tier=tier
            )
            cal = calibration_for_bucket(
                store, paths,
                tenant_id=args.tenant, work_type=work_type, tier=tier,
            )
            shadow_n = len({
                d.card_id for d in shadow
                if d.work_type == work_type and d.tier == tier
            })
            rec = evaluate_advance(
                state, cal,
                shadow_n=shadow_n,
                live_n=count_live_decisions(
                    paths, tenant_id=args.tenant,
                    work_type=work_type, tier=tier,
                ),
                killswitch_clear=ks_quiet,
            )
            rows.append((state, shadow_n, rec))
    finally:
        repo.close()

    if args.json:
        print(json.dumps({
            "tenant": args.tenant,
            "buckets": [
                {
                    "work_type": state.work_type,
                    "tier": state.tier,
                    "phase": state.phase,
                    "alarm_active": state.alarm_active,
                    "shadow_n": shadow_n,
                    "advance_ready": rec.ready,
                    "checks": [
                        {"name": c.name, "passed": c.passed,
                         "detail": c.detail}
                        for c in rec.checks
                    ],
                }
                for state, shadow_n, rec in rows
            ],
        }, indent=2))
        return 0
    if not rows:
        print(f"no gate buckets for tenant '{args.tenant}'")
        return 0
    for state, shadow_n, rec in rows:
        flag = " ALARM" if state.alarm_active else ""
        ready = "ready to advance" if rec.ready else "not ready"
        print(
            f"{state.work_type}/tier{state.tier}: phase {state.phase}"
            f"{flag}  shadow_n={shadow_n}  {ready}"
        )
        for check in rec.checks:
            mark = "ok " if check.passed else "no "
            print(f"  [{mark}] {check.name}: {check.detail}")
    return 0


def _cmd_stats_ramp_advance(args: argparse.Namespace) -> int:
    """`cards-runner stats ramp advance --bucket work_type:tier --confirm`.

    Operator-explicit phase advancement (spec 9.3). Evaluates the
    gates, emits a `gate_phase_recommendation` event either way, and
    only with --confirm AND all gates green applies the +1 and emits
    `gate_phase_advanced`. Exit codes: 0 advanced (or dry-run ready),
    1 gates not met, 2 usage error."""
    from ..common.types import now_utc_iso
    from ..metrics.calibration import (
        calibration_for_bucket, read_shadow_decisions,
    )
    from ..metrics.ramp import (
        RampStore, count_live_decisions, evaluate_advance,
        killswitch_quiet,
    )
    from ..metrics.store import MetricsStore
    from ..metrics.writer import LedgerWriter

    try:
        work_type, tier = _parse_bucket(args.bucket)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    paths = RuntimePaths.from_root(args.todo_root)
    repo = _open_store(args)
    try:
        store = MetricsStore.from_repository(repo)
        ramp = RampStore.from_repository(repo)
        writer = LedgerWriter(paths, store)
        state = ramp.get(
            tenant_id=args.tenant, work_type=work_type, tier=tier
        )
        cal = calibration_for_bucket(
            store, paths,
            tenant_id=args.tenant, work_type=work_type, tier=tier,
        )
        shadow_n = len({
            d.card_id
            for d in read_shadow_decisions(paths, tenant_id=args.tenant)
            if d.work_type == work_type and d.tier == tier
        })
        rec = evaluate_advance(
            state, cal,
            shadow_n=shadow_n,
            live_n=count_live_decisions(
                paths, tenant_id=args.tenant,
                work_type=work_type, tier=tier,
            ),
            killswitch_clear=killswitch_quiet(
                paths, tenant_id=args.tenant
            ),
        )
        at = now_utc_iso()
        writer.record_gate_phase_recommendation(
            tenant_id=args.tenant, work_type=work_type, tier=tier,
            current_phase=rec.current_phase, next_phase=rec.next_phase,
            ready=rec.ready,
            checks=[
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in rec.checks
            ],
            at=at,
        )
        print(
            f"{work_type}/tier{tier}: phase {rec.current_phase} -> "
            f"{rec.next_phase}  "
            f"{'gates green' if rec.ready else 'gates NOT met'}"
        )
        for check in rec.checks:
            mark = "ok " if check.passed else "no "
            print(f"  [{mark}] {check.name}: {check.detail}")
        if not rec.ready:
            return 1
        if not args.confirm:
            print("dry run: pass --confirm to apply")
            return 0
        new_state = ramp.set_phase(
            tenant_id=args.tenant, work_type=work_type, tier=tier,
            phase=rec.next_phase,
        )
        ramp.commit()
        writer.record_gate_phase_advanced(
            tenant_id=args.tenant, work_type=work_type, tier=tier,
            from_phase=rec.current_phase, to_phase=new_state.phase,
            at=at,
        )
        print(f"advanced: now phase {new_state.phase}")
        return 0
    finally:
        repo.close()


def _cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor as _doctor

    cfg = DaemonConfig(
        todo_root=args.todo_root,
        store_spec=args.store or "",
    )
    repo: CardRepository | None = None
    if not args.skip_store:
        try:
            repo = _open_store(args)
        except Exception as exc:  # noqa: BLE001
            print(
                f"warning: could not open card store ({exc}); "
                "running doctor with --skip-store semantics",
                file=sys.stderr,
            )
    try:
        report = _doctor.build_report(
            cfg,
            repo=repo,
            dolt_bin_env=os.environ.get("CARDS_DOLT_BIN"),
        )
    finally:
        if repo is not None:
            repo.close()
    if args.json:
        print(_doctor.render_json(report))
    else:
        print(_doctor.render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
