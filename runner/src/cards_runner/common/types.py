"""Shared dataclasses and constants.

Kept narrow on purpose. The daemon and worker both depend on these
types; anything richer lives in its owning subpackage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final


# Canonical v1 subfolder names per RUNNER_CONTRACT.md "Directory
# invariants". After the chunk 2b cutover the database is canonical
# and the runner no longer keeps folder-as-state; these names survive
# only so `store.migrate_v1` can walk a legacy v1 tree.
SUBFOLDER_BACKLOG: Final[str] = "backlog"
SUBFOLDER_ACTIVE: Final[str] = "active"
SUBFOLDER_AMENDMENTS: Final[str] = "amendments"
SUBFOLDER_AWAITING_STANDUP: Final[str] = "awaiting_standup_review"
SUBFOLDER_DONE: Final[str] = "done"
SUBFOLDER_BLOCKED: Final[str] = "blocked"

ALL_SUBFOLDERS: Final[tuple[str, ...]] = (
    SUBFOLDER_BACKLOG,
    SUBFOLDER_ACTIVE,
    SUBFOLDER_AMENDMENTS,
    SUBFOLDER_AWAITING_STANDUP,
    SUBFOLDER_DONE,
    SUBFOLDER_BLOCKED,
)

# Where the per-attempt runtime data lives, relative to TODO root.
RUNS_DIRNAME: Final[str] = "_runs"

# Where preapproval markers live, relative to TODO root.
SIGNALS_DIRNAME: Final[str] = "_signals"

# Daemon singleton lock.
DAEMON_LOCK_NAME: Final[str] = ".daemon.lock"

# Global worktree-creation mutex.
RUNNER_LOCK_NAME: Final[str] = ".runner.lock"

# Per-worktree halt sentinel (chunk 2b-ii cost-cap fallback).
HALT_SENTINEL: Final[str] = ".cards-halt"

# The per-run projected card file name. The runner projects a claimed
# card into `_runs/<attempt>/` under this name; the worker reads and
# writes that file exactly as v1 workers read a card in `active/`.
PROJECTED_CARD_NAME: Final[str] = "card.md"

# The worker's structured result sidecar, written into the run dir on
# exit. The daemon reads it in `_post_worker_exit` to enrich the
# `executed` event payload with token/cost/cascade detail. It is a
# transient runtime artifact, not card state -- the card store stays
# canonical and never carries derived USD (RUNNER_CONTRACT.md).
WORKER_RESULT_NAME: Final[str] = "result.json"

# Worker-side heartbeat file inside the worktree.
HEARTBEAT_FILE: Final[str] = ".cards-heartbeat"


def now_utc_iso() -> str:
    """ISO 8601 UTC timestamp with trailing Z, second resolution.

    Matches what the planner writes in `started_at`, `last_heartbeat`,
    etc. Second resolution is enough for orphan reclaim decisions and
    keeps the field human-readable.
    """
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp written by the runner. None passes through.

    Accepts both `...Z` and explicit `+00:00`. Returns timezone-aware
    UTC datetimes. Raises ValueError on malformed input rather than
    silently coercing to None.
    """
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class RuntimePaths:
    """The disk paths the daemon and workers still reference.

    After the chunk 2b cutover the database is canonical: card state
    is a `status` column, not a subfolder, so the `backlog/active/...`
    tree is gone. What remains on disk is genuinely runtime-only --
    the per-attempt run dirs, preapproval signal markers, and the two
    coordination locks. The card store itself lives wherever the
    store spec points (default `<todo_root>/cards.db`).
    """

    todo_root: Path
    runs: Path
    signals: Path
    daemon_lock: Path
    runner_lock: Path

    @classmethod
    def from_root(cls, todo_root: Path) -> "RuntimePaths":
        root = todo_root.resolve()
        return cls(
            todo_root=root,
            runs=root / RUNS_DIRNAME,
            signals=root / SIGNALS_DIRNAME,
            daemon_lock=root / DAEMON_LOCK_NAME,
            runner_lock=root / RUNNER_LOCK_NAME,
        )

    def ensure(self) -> None:
        """Create the runtime directory layout if missing. Idempotent.

        The daemon calls this at boot. Only the runtime dirs are made;
        the card store creates its own file.
        """
        for d in (self.todo_root, self.runs, self.signals):
            d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DaemonConfig:
    """Daemon-wide runtime knobs.

    Constructed from CLI flags. Project-level knobs (cascade thresholds,
    etc.) live on the per-card project config, not here.
    """

    todo_root: Path
    store_spec: str = ""  # "sqlite:PATH" / "dolt:DIR"; empty -> default.
    poll_interval_sec: float = 5.0
    max_parallel: int = 4
    max_parallel_pinned: int = 1
    orphan_timeout_minutes: int = 120
    heartbeat_interval_sec: float = 30.0
    worktree_forensic_ttl_hours: int = 24
    stub_sleep_sec: float = 3.0  # how long the stub worker sleeps.
    force_kill_after_seconds: int = 90
    skip_worktree: bool = False  # tests bypass git when not on a real repo.
    # Which executor the spawned worker runs: "stub" (chunk 1 default,
    # zero tokens), "sdk" (chunk 2b-ii reasoning-only SdkInvoker), or
    # "sdk-tools" (chunk 3 tool-using SdkInvoker -- file/shell/git tool
    # belt). Any SDK mode makes the daemon inject ANTHROPIC_API_KEY
    # into the worker's scrubbed env block.
    invoker: str = "stub"
    # Verifier configuration. Per RUNNER_CONTRACT.md "Cold-read
    # verification". `verifier_enabled=False` falls back to chunk 2's
    # behavior (clean rc=0 leaves the card active for the next claim);
    # tests use it to keep the executor / verifier suites separate.
    verifier_enabled: bool = True
    verifier_cascade_disabled: bool = False
    verifier_skip_confidence_threshold: float = 0.9
    subjective_confidence_threshold: float = 0.85
    subjective_starting_tier: str = "haiku"
    subjective_max_tier: str = "opus"
    # Chunk 4 merge gate. Default OFF so the chunk-3 tests (and any
    # caller that hasn't wired GitHub yet) keep their existing
    # "verifier pass -> done" behavior. When enabled, the verifier-pass
    # transition routes through the tier-aware gate: tier 1-2 + no pin
    # auto-merge via `gh pr merge --auto`, tier 3-4 open a PR awaiting
    # sibling review, tier 5-6 / pinned open a PR awaiting human merge.
    pr_gate_enabled: bool = False
    gh_path: str = "gh"
    git_path: str = "git"
    auto_merge_strategy: str = "squash"
    pr_base_branch_default: str = "main"
    # Boot-time worker-alive check (chunk 4). When True the daemon does
    # an os-level liveness check on the recorded PIDs for active cards
    # at boot and reclaims any whose process is no longer alive --
    # faster than waiting for the orphan timeout.
    boot_worker_alive_check: bool = True
    # Chunk 5 unblocker / reviewer / project-config toggles. Each is
    # off-by-default so chunk-4 callers see no behavior change; the
    # operator opts in via the CLI flag once the project is ready.
    pr_unblock_enabled: bool = False
    sibling_reviewer_enabled: bool = False
    amendment_reviewer_enabled: bool = False
    worktree_prune_enabled: bool = False
    # How often to run `git worktree prune`, in seconds. Default hourly
    # because the cost (a couple of subprocess calls) is small but the
    # benefit (cleaning up dead refs accumulated by chunk-3-era git work)
    # is rare in steady state.
    worktree_prune_interval_sec: int = 3600
    project_config_path: Path | None = None
    log_dir: Path | None = None

    @property
    def orphan_timeout_sec(self) -> int:
        return int(self.orphan_timeout_minutes * 60)

    def resolved_store_spec(self) -> str:
        """The store spec to use, falling back to the SQLite default.

        Kept as a method (not resolved at construction) so the default
        always tracks `todo_root` even if a caller mutates nothing.
        """
        if self.store_spec:
            return self.store_spec
        return f"sqlite:{self.todo_root / 'cards.db'}"


@dataclass
class CardSnapshot:
    """Everything we read from a card on disk in one shot.

    Mutable: the daemon updates the frontmatter dict in place and
    writes the snapshot back. The file path itself is not on this
    type because the snapshot survives subfolder moves.
    """

    card_id: str
    frontmatter: dict[str, Any]
    body: str
    raw_frontmatter_text: str = ""

    def get(self, key: str, default: Any = None) -> Any:
        return self.frontmatter.get(key, default)


@dataclass(frozen=True)
class ClaimedCard:
    """A card the daemon has successfully claimed from the store.

    The claim is now a transactional `UPDATE` in the card store, not
    an atomic file move. What the daemon carries forward is the
    per-attempt identity plus the disk paths the worker needs: the run
    dir, the git worktree inside it, and the projected card `.md` file
    the worker reads and writes (a per-run view, not canonical state).
    The card's authoritative record lives in the store.
    """

    card_id: str
    attempt_trace_id: str
    trace_id: str
    run_dir: Path
    worktree_path: Path
    card_file: Path


# Exit codes from worker processes. Documented here so the daemon
# can route on them without magic numbers.
EXIT_CLEAN: Final[int] = 0
EXIT_STUB_ERROR: Final[int] = 10
EXIT_COST_CAP_HALT: Final[int] = 11  # reserved for chunk 2.
EXIT_HALT_SIGNAL: Final[int] = 12  # reserved for chunk 2.
EXIT_UNCAUGHT: Final[int] = 99
