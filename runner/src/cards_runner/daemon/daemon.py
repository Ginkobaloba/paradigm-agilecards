"""The daemon main loop, store-backed.

Responsibilities:

- Singleton enforcement via `.daemon.lock` (file + PID).
- Boot sanity: open the card store, initialize the schema, reconcile
  `active` cards left by a prior daemon.
- Polling loop: query the store for `backlog` cards, claim eligible
  ones transactionally, project each into a per-run card file, spawn
  a worker, mirror worker heartbeats into the store, reclaim
  orphans, and land worker results back into the store on exit.
- Clean shutdown: receive a stop signal, drain workers, release the
  singleton lock, close the store.

Per the cards-are-state principle the daemon holds NO durable card
state of its own. The store is the single source of truth; the map
of `attempt_trace_id -> _WorkerHandle` is an in-memory cache of live
subprocesses only. If the daemon dies, the next boot reads `active`
cards from the store and orphan-reclaims any whose heartbeat went
stale.

This is the chunk 2b cutover. v1's claim was an atomic file move
(`backlog/` -> `active/`) plus an in-place frontmatter stamp,
arbitrated by `os.replace`. The claim is now a transactional
conditional `UPDATE` in the card store. The atomic-rename sentinel,
the `max_parallel` demotion it drove, and the malformed-claim
boot-reconcile window are gone: a transactional claim cannot
half-succeed the way "move then stamp" could.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Optional

from ..common.locks import FileLock, LockHeldError, pid_alive
from ..common.logging_setup import setup_daemon_logging
from ..common.process_group import ManagedProcess
from ..common.project_config import (
    ProjectConfig,
    ProjectConfigLoader,
    resolve_project_config_path,
)
from ..common.types import (
    EXIT_COST_CAP_HALT,
    EXIT_HALT_SIGNAL,
    PROJECTED_CARD_NAME,
    WORKER_RESULT_NAME,
    ClaimedCard,
    DaemonConfig,
    RuntimePaths,
    now_utc_iso,
)
from ..store import (
    DEFAULT_TENANT,
    ActorType,
    CardEvent,
    CardRecord,
    CardRepository,
    CardStatus,
    EventType,
    build_repository,
)
from ..metrics.store import MetricsStore
from ..metrics.writer import LedgerWriter
from ..store.projection import ProjectionError, parse_card_text, project_card_file
from ..verifier import VerifierError, VerifierResult, verify_card
from ..verifier.runner import VERDICT_FAIL, VERDICT_PASS, VERDICT_STANDUP
from .eligibility import EligibilityResult, evaluate_eligibility
from .merge_gate import MergeGate, MergeOutcome, build_default_gh_runner
from .amendment_editor_client import (
    AmendmentEditorClient,
    AnthropicAmendmentEditorClient,
)
from .amendment_reviewer import run_amendment_reviews
from .orphan import reclaim, scan_for_orphans
from .pr_lifecycle import GhRunner
from .reaper import reap_forensic_run_dirs
from .signals_cleanup import sweep_reviewer_markers
from .sibling_reviewer import (
    AnthropicSiblingReviewerClient,
    SiblingReviewerClient,
    StaticSiblingReviewerClient,
    run_sibling_reviews,
)
from .spawner import spawn_worker
from .unblocker import UnblockDecision, unblock_merged_cards
from .worktree import WorktreeCreateError, prepare_worktree, prune_git_worktrees


# How many times the daemon re-runs the verifier on a `VerifierError`
# before giving up and routing to `blocked`. Per
# RUNNER_CONTRACT.md "Result shape" / error branch: "retries the
# verifier up to two times. After two failed retries, the card moves
# to `blocked/`".
_VERIFIER_MAX_RETRIES: int = 2


log = logging.getLogger(__name__)


class DaemonAlreadyRunning(Exception):
    """Raised when a second daemon tries to boot while one is live."""


@dataclass
class _WorkerHandle:
    claim: ClaimedCard
    process: ManagedProcess
    spawned_at: float


class Daemon:
    """The runner daemon.

    Single instance per host. Constructed with a `DaemonConfig`. An
    already-open `CardRepository` may be injected (tests do this so
    the daemon shares the test's connection); otherwise the daemon
    opens its own from `cfg.resolved_store_spec()` at boot and owns
    its lifecycle. `run()` blocks until `stop()` is called or a
    SIGTERM-style signal arrives.
    """

    def __init__(
        self,
        cfg: DaemonConfig,
        *,
        repo: CardRepository | None = None,
        gh: GhRunner | None = None,
        project_config_loader: ProjectConfigLoader | None = None,
        sibling_reviewer_client: SiblingReviewerClient | None = None,
        amendment_reviewer_client: SiblingReviewerClient | None = None,
        amendment_editor_client: AmendmentEditorClient | None = None,
    ) -> None:
        self.cfg = cfg
        self.paths = RuntimePaths.from_root(cfg.todo_root)
        self.paths.ensure()
        self.tenant_id = DEFAULT_TENANT
        self._workers: dict[str, _WorkerHandle] = {}
        self._stop_event = threading.Event()
        self._singleton_lock: FileLock | None = None
        self._repo: CardRepository | None = repo
        self._owns_repo = repo is None
        self._last_tick_at: float = 0.0
        self._last_tick_summary: dict[str, int] = {}
        self._last_prune_at: float = 0.0
        # Merge gate. Tests pass a `FakeGhRunner`; production builds use
        # the default subprocess wrapper. When `pr_gate_enabled=False`
        # both the gate and the runner are no-ops by design.
        self._gh: GhRunner = gh if gh is not None else build_default_gh_runner(cfg)
        # Project config (chunk 5). Tests pass their own loader for
        # explicit control; production resolves a default path from
        # `cfg.project_config_path` or `<todo_root>/project.yaml`.
        if project_config_loader is not None:
            self._project_loader = project_config_loader
        else:
            resolved = resolve_project_config_path(
                cfg.project_config_path, todo_root=self.paths.todo_root
            )
            self._project_loader = ProjectConfigLoader(resolved)
        self._merge_gate = MergeGate(cfg=cfg, gh=self._gh)
        self._sibling_reviewer_client = sibling_reviewer_client
        self._amendment_reviewer_client = amendment_reviewer_client
        self._amendment_editor_client = amendment_editor_client
        # Ledger chunk 2 writer. Built lazily on first use (it needs the
        # store connection, which `_boot` opens), and only when
        # `cfg.ledger_enabled`. None means metrics recording is off.
        self._ledger: LedgerWriter | None = None

    @property
    def project_config(self) -> ProjectConfig:
        return self._project_loader.current()

    # ---- lifecycle ----------------------------------------------------

    def run(self) -> int:
        """Boot the daemon and block on the polling loop.

        Returns the process exit code (0 clean, non-zero on error).
        """
        log_dir = self.cfg.log_dir or self.paths.runs
        setup_daemon_logging(log_dir)
        try:
            self._acquire_singleton()
        except DaemonAlreadyRunning as exc:
            log.error("%s", exc)
            return 2

        try:
            self._install_signal_handlers()
            self._boot()
            self._loop()
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt; stopping")
        except Exception:
            log.exception("daemon main loop crashed")
            return 1
        finally:
            self._drain_workers()
            if self._singleton_lock is not None:
                self._singleton_lock.release()
            self._close_repo()
        return 0

    def stop(self) -> None:
        """Signal the polling loop to exit.

        Safe to call from a signal handler.
        """
        log.info("stop requested; signaling loop")
        self._stop_event.set()

    def close(self) -> None:
        """Release the owned store connection. For tests that drive
        `_boot()` / `_tick()` directly without `run()`."""
        self._close_repo()

    @property
    def repo(self) -> CardRepository:
        if self._repo is None:
            raise RuntimeError("daemon store not open; call _boot() first")
        return self._repo

    @property
    def effective_max_parallel(self) -> int:
        # The transactional claim is correct under concurrency, so
        # there is no longer a sentinel that can demote parallelism.
        return self.cfg.max_parallel

    @property
    def last_tick_summary(self) -> dict[str, int]:
        return dict(self._last_tick_summary)

    @property
    def last_tick_at(self) -> float:
        return self._last_tick_at

    def _close_repo(self) -> None:
        if self._owns_repo and self._repo is not None:
            try:
                self._repo.close()
            except Exception:  # noqa: BLE001
                log.exception("error closing store")
            self._repo = None

    # ---- singleton lock ----------------------------------------------

    def _acquire_singleton(self) -> None:
        lock = FileLock(self.paths.daemon_lock)
        try:
            lock.acquire(blocking=False)
        except LockHeldError:
            prior_pid = lock.read_pid()
            if prior_pid is not None and pid_alive(prior_pid):
                raise DaemonAlreadyRunning(
                    f"another daemon holds {self.paths.daemon_lock} "
                    f"(pid={prior_pid}); refusing to start"
                )
            # Stale lock. Retry once after writing our own PID. The
            # OS released the OS-level lock when the prior process
            # died; we just lost the race.
            try:
                lock.acquire(blocking=False)
            except LockHeldError as exc:
                raise DaemonAlreadyRunning(
                    f"could not acquire stale singleton lock: {exc}"
                ) from exc
        lock.write_pid(os.getpid())
        self._singleton_lock = lock
        log.info("singleton lock acquired pid=%d", os.getpid())

    def _install_signal_handlers(self) -> None:
        def handler(signum: int, frame: Optional[FrameType]) -> None:
            del frame
            log.info("received signal %d; stopping", signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except ValueError:
            # signal() raises when called from a non-main thread; the
            # daemon runs on the main thread in production. Tests may
            # construct a Daemon from a fixture thread.
            pass

    # ---- boot reconciliation -----------------------------------------

    def _boot(self) -> None:
        log.info(
            "daemon booting todo_root=%s poll=%.1fs max_parallel=%d",
            self.paths.todo_root, self.cfg.poll_interval_sec, self.cfg.max_parallel,
        )
        if self._repo is None:
            spec = self.cfg.resolved_store_spec()
            log.info("opening card store %s", spec)
            self._repo = build_repository(spec)
        self._repo.initialize_schema()

        # Reconcile any cards left `active` by a prior daemon. We hold
        # no worker for them (fresh boot), so the only question is
        # heartbeat staleness. A card whose heartbeat aged past the
        # orphan window is reclaimed; a card with a fresh heartbeat is
        # left alone for the poll loop to re-evaluate. There is no
        # malformed-claim repair path anymore: the transactional claim
        # stamps every claim field or none of them.
        for card_id in scan_for_orphans(
            repo=self.repo, cfg=self.cfg, tenant_id=self.tenant_id
        ):
            try:
                reclaim(self.repo, card_id, tenant_id=self.tenant_id)
            except Exception as exc:  # noqa: BLE001
                log.error("boot reclaim failed for %s: %s", card_id, exc)

        # Chunk 4 boot-time worker-alive check. The orphan scan above
        # only catches stale heartbeats; a daemon that crashed and
        # restarted quickly can leave `active` cards with fresh
        # heartbeats and no live worker behind them. The alive check
        # walks each active card, looks up its recorded worker pid in
        # `_runs/<attempt>/worker.pid`, and reclaims any whose pid is
        # no longer alive -- without waiting for the heartbeat to age.
        if self.cfg.boot_worker_alive_check:
            self._boot_alive_check()

    def _boot_alive_check(self) -> None:
        """Reclaim active cards whose worker pid is no longer alive.

        Reads `_runs/<attempt>/worker.pid` for each card; missing
        pidfile is logged but NOT treated as dead (a daemon restart
        immediately after a worker spawn might race the spawner's
        pidfile write). A pidfile whose pid is unparseable is treated
        as dead and reclaims the card.
        """
        for record in self.repo.query_cards(
            tenant_id=self.tenant_id, status=CardStatus.ACTIVE.value
        ):
            attempt_id = record.attempt_trace_id
            if not attempt_id:
                continue
            pidfile = self.paths.runs / attempt_id / "worker.pid"
            if not pidfile.is_file():
                log.debug(
                    "no worker.pid for active card %s (attempt %s); "
                    "deferring to heartbeat path",
                    record.card_id, attempt_id,
                )
                continue
            try:
                pid = int(pidfile.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                log.warning(
                    "worker.pid for %s unparseable; reclaiming card",
                    record.card_id,
                )
                self._safe_reclaim(record.card_id)
                continue
            if not pid_alive(pid):
                log.info(
                    "boot alive check: worker pid %d for card %s is dead; "
                    "reclaiming early",
                    pid, record.card_id,
                )
                self._safe_reclaim(record.card_id)

    def _safe_reclaim(self, card_id: str) -> None:
        try:
            reclaim(self.repo, card_id, tenant_id=self.tenant_id)
        except Exception as exc:  # noqa: BLE001
            log.error("safe reclaim failed for %s: %s", card_id, exc)

    # ---- polling loop -------------------------------------------------

    def _loop(self) -> None:
        log.info("entering poll loop")
        while not self._stop_event.is_set():
            tick_started = time.monotonic()
            try:
                self._tick()
            except Exception:
                log.exception("tick failed; continuing")
            self._last_tick_at = time.time()
            elapsed = time.monotonic() - tick_started
            sleep_for = max(0.0, self.cfg.poll_interval_sec - elapsed)
            if self._stop_event.wait(timeout=sleep_for):
                break

    def _tick(self) -> None:
        summary: dict[str, int] = {
            "active_before": 0,
            "orphans_reclaimed": 0,
            "claims": 0,
            "worker_exits": 0,
            "run_dirs_reaped": 0,
            "unblocked_to_done": 0,
            "sibling_reviews": 0,
            "amendment_reviews": 0,
        }
        # Chunk 5: reload project.yaml at the top of every tick. A
        # bumped mtime triggers a re-read; no change is a single
        # stat() call.
        self._project_loader.reload_if_changed()

        # 0. Reap forensic run-dirs whose TTL has expired. Cheap; runs
        #    every tick because the cost of a missed sweep is unbounded
        #    disk growth. Skip silently when the TTL is non-positive.
        try:
            for decision in reap_forensic_run_dirs(
                repo=self.repo,
                cfg=self.cfg,
                paths=self.paths,
                in_flight_attempts=set(self._workers.keys()),
                tenant_id=self.tenant_id,
            ):
                if decision.action == "reaped":
                    summary["run_dirs_reaped"] += 1
        except Exception:  # noqa: BLE001
            log.exception("forensic reaper failed; continuing")

        # 0.5 Sweep stale reviewer markers (chunk 6d). Same cadence + the
        #     same "best-effort, retry next tick" semantics as the
        #     forensic reaper. No-op when reviewer_marker_ttl_hours <= 0.
        try:
            for marker_decision in sweep_reviewer_markers(
                repo=self.repo,
                cfg=self.cfg,
                paths=self.paths,
                tenant_id=self.tenant_id,
            ):
                if marker_decision.action in ("removed", "removed_orphan"):
                    summary["marker_files_swept"] = (
                        summary.get("marker_files_swept", 0) + 1
                    )
        except Exception:  # noqa: BLE001
            log.exception("reviewer-marker sweep failed; continuing")

        # 0a. Chunk 5: unblock cards whose external PR has merged. The
        #     merge gate parks awaiting-merge cards in `blocked` with a
        #     pr_url; this poll promotes them to `done` once gh reports
        #     the PR landed. No-op when `pr_unblock_enabled=False`.
        try:
            decisions = unblock_merged_cards(
                repo=self.repo,
                gh=self._gh,
                cfg=self.cfg,
                actor_id=f"cards-runner-daemon@pid{os.getpid()}",
                tenant_id=self.tenant_id,
            )
            for d in decisions:
                if d.action == "unblocked":
                    summary["unblocked_to_done"] += 1
                    self._record_pr_merged_metrics(d)
        except Exception:  # noqa: BLE001
            log.exception("pr unblocker failed; continuing")

        # 0b. Chunk 5: `git worktree prune` sweep. The chunk-4 reaper
        #     deletes the `_runs/<attempt>/` tree; this drops the dead
        #     administrative `.git/worktrees/<id>/` entries that the
        #     project repo still tracks. Rate-limited by
        #     `worktree_prune_interval_sec`; off by default.
        if self.cfg.worktree_prune_enabled:
            self._maybe_prune_git_worktrees()

        # 0c. Chunk 5: sibling-agent reviewer for tier-3/4 PRs.
        #     Walks blocked/requires_review cards and posts a gh review.
        #     No-op when the host knob or the project knob is False.
        try:
            outcomes = run_sibling_reviews(
                repo=self.repo,
                gh=self._gh,
                cfg=self.cfg,
                paths=self.paths,
                reviewer_client=self._build_sibling_reviewer_client(),
                reviewer_config=self.project_config.sibling_reviewer,
                tenant_id=self.tenant_id,
            )
            for o in outcomes:
                if o.action == "reviewed":
                    summary["sibling_reviews"] += 1
        except Exception:  # noqa: BLE001
            log.exception("sibling reviewer sweep failed; continuing")

        # 0d. Chunk 5: AC-amendment reviewer for `amendments` cards.
        #     Walks amendments-status cards, reviews their change_request
        #     blocks, and routes approve/deny/comment. No-op when both
        #     toggles are off.
        try:
            amend_outcomes = run_amendment_reviews(
                repo=self.repo,
                cfg=self.cfg,
                paths=self.paths,
                reviewer_client=self._build_amendment_reviewer_client(),
                reviewer_config=self.project_config.amendment_reviewer,
                editor_client=self._build_amendment_editor_client(),
                tenant_id=self.tenant_id,
            )
            for o in amend_outcomes:
                if o.action.startswith("reviewed_"):
                    summary["amendment_reviews"] += 1
        except Exception:  # noqa: BLE001
            log.exception("amendment reviewer sweep failed; continuing")

        # 1. Reap exited workers; this frees parallelism slots and
        #    lands their results in the store.
        self._reap_workers(summary)

        # 2. Mirror live workers' liveness into the store so a stale
        #    heartbeat genuinely means a dead worker. Done before the
        #    orphan scan so a card with a live worker is never seen
        #    as orphaned.
        self._bump_heartbeats()

        # 3. Orphan scan against the store.
        for card_id in scan_for_orphans(
            repo=self.repo, cfg=self.cfg, tenant_id=self.tenant_id
        ):
            handle = self._find_handle_for_card(card_id)
            if handle is not None:
                log.warning(
                    "killing stale worker for %s before reclaim", card_id
                )
                handle.process.kill_tree(grace_sec=2.0)
                self._workers.pop(handle.claim.attempt_trace_id, None)
            try:
                reclaim(self.repo, card_id, tenant_id=self.tenant_id)
                summary["orphans_reclaimed"] += 1
            except Exception as exc:  # noqa: BLE001
                log.error("reclaim failed for %s: %s", card_id, exc)

        # 4. Claim new cards up to capacity.
        summary["active_before"] = len(
            self.repo.query_cards(
                tenant_id=self.tenant_id, status=CardStatus.ACTIVE.value
            )
        )
        capacity = self.effective_max_parallel - len(self._workers)
        for record in self.repo.query_cards(
            tenant_id=self.tenant_id, status=CardStatus.BACKLOG.value
        ):
            if capacity <= 0:
                break
            if not self._is_eligible(record):
                continue
            claim = self._try_claim(record)
            if claim is None:
                continue
            if not self._prepare_and_spawn(claim):
                continue
            summary["claims"] += 1
            capacity -= 1

        self._last_tick_summary = summary

    def _is_eligible(self, record: CardRecord) -> bool:
        """Real claim eligibility (chunk 4).

        Delegates to `eligibility.evaluate_eligibility`, which encodes
        RUNNER_CONTRACT.md's claim protocol:

        1. Every entry in the card's `depends_on` must be in `done`
           with `merge_status: merged` (the contract's "every
           dependency must be in `done/` with `merge_status: merged`").
        2. If a story source path is reachable, the file's current
           sha256 must match the card's `story_hash`. A mismatch
           transitions the card to `blocked` ("On mismatch, refuses
           the claim and moves the card to `blocked/`") and is logged.

        The pre-approval gate (`requires_pre_approval: true`) reads a
        signal marker. Chunk 4 implements the same marker scheme the
        contract suggests: `signals_dir/preapproval/<card_id>.ok`. The
        card is held in `backlog` until the marker exists.
        """
        outcome = evaluate_eligibility(
            record,
            repo=self.repo,
            cfg=self.cfg,
            paths=self.paths,
            tenant_id=self.tenant_id,
            project_config=self.project_config,
        )
        if outcome.action == "claim":
            return True
        if outcome.action == "block":
            self._route_eligibility_block(record, outcome)
            return False
        if outcome.reason:
            log.debug(
                "card %s not eligible: %s", record.card_id, outcome.reason
            )
        return False

    def _route_eligibility_block(
        self, record: CardRecord, outcome: EligibilityResult
    ) -> None:
        """Transition a backlog card to `blocked` for an eligibility reason.

        Today only story drift triggers this path; the dependency-gating
        and pre-approval checks defer (the card stays in backlog and is
        re-evaluated next tick, since `depends_on` and approval markers
        are transient by nature). Story drift, however, is permanent
        until a `/cards` re-triage; routing to blocked stops the runner
        from re-checking it every tick.
        """
        log.warning(
            "routing card_id=%s to blocked (eligibility): %s",
            record.card_id, outcome.reason,
        )
        try:
            self.repo.transition(
                record.card_id,
                to_status=CardStatus.BLOCKED.value,
                tenant_id=self.tenant_id,
                fields={
                    "merge_status": "blocked",
                },
                actor_id=f"cards-runner-daemon@pid{os.getpid()}",
                actor_type=ActorType.RUNNER.value,
                event_type=EventType.BLOCKED.value,
                payload={
                    "reason": outcome.reason,
                    "kind": outcome.kind,
                    "detail": outcome.detail or {},
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "could not route %s to blocked on eligibility: %s",
                record.card_id, exc,
            )

    def _try_claim(self, record: CardRecord) -> ClaimedCard | None:
        attempt_trace_id = os.urandom(16).hex()
        try:
            claimed = self.repo.claim_card(
                record.card_id,
                claimed_by=f"cards-runner-daemon@pid{os.getpid()}",
                attempt_trace_id=attempt_trace_id,
                tenant_id=self.tenant_id,
            )
        except Exception:
            log.exception("unexpected error claiming %s", record.card_id)
            return None
        if claimed is None:
            log.debug("claim lost / not claimable: %s", record.card_id)
            return None
        run_dir = self.paths.runs / attempt_trace_id
        return ClaimedCard(
            card_id=claimed.card_id,
            attempt_trace_id=attempt_trace_id,
            trace_id=str(claimed.trace_id or attempt_trace_id),
            run_dir=run_dir,
            worktree_path=run_dir / "worktree",
            card_file=run_dir / PROJECTED_CARD_NAME,
        )

    def _prepare_and_spawn(self, claim: ClaimedCard) -> bool:
        claim.run_dir.mkdir(parents=True, exist_ok=True)

        record = self.repo.get_card(claim.card_id, tenant_id=self.tenant_id)
        if record is None:  # pragma: no cover - just claimed it.
            log.error("claimed card %s vanished before spawn", claim.card_id)
            return False

        project = record.project
        branch = record.field_value("branch") or f"card/{claim.card_id}"
        base = record.field_value("base_branch") or "main"
        try:
            prepare_worktree(
                paths=self.paths,
                project_dir=Path(project) if project else self.paths.todo_root,
                branch_name=str(branch),
                base_branch=str(base),
                worktree_path=claim.worktree_path,
                skip_git=self.cfg.skip_worktree,
            )
        except WorktreeCreateError as exc:
            log.error(
                "worktree prep failed for %s: %s; rolling back",
                claim.card_id, exc,
            )
            self._rollback_to_backlog(claim)
            return False

        # Project the claimed card into the per-run dir. The worker
        # reads and writes this file; it is an ephemeral per-run view,
        # not canonical state. The store row stays authoritative.
        try:
            project_card_file(record, claim.card_file, verbatim=False)
        except Exception:
            log.exception("card projection failed for %s", claim.card_id)
            self._rollback_to_backlog(claim)
            return False

        try:
            process = spawn_worker(
                cfg=self.cfg,
                claim=claim,
                run_dir=claim.run_dir,
            )
        except Exception:
            log.exception("worker spawn failed for %s", claim.card_id)
            self._rollback_to_backlog(claim)
            return False

        self._workers[claim.attempt_trace_id] = _WorkerHandle(
            claim=claim,
            process=process,
            spawned_at=time.monotonic(),
        )
        return True

    def _rollback_to_backlog(self, claim: ClaimedCard) -> None:
        """Return a half-prepared claim to `backlog` in the store."""
        try:
            reclaim(self.repo, claim.card_id, tenant_id=self.tenant_id)
        except Exception:  # noqa: BLE001
            log.exception("rollback failed for %s", claim.card_id)

    # ---- worker lifecycle --------------------------------------------

    def _bump_heartbeats(self) -> None:
        """Mirror each live worker's liveness into the store.

        The worker writes its own heartbeat into its projected card
        file (and touches the worktree heartbeat file). The store --
        what orphan reclaim reads -- is the runner's to write, so the
        daemon stamps `last_heartbeat` for every card whose worker
        process the OS still reports as alive. The heartbeat going
        stale is therefore exactly equivalent to the worker dying.
        """
        now = now_utc_iso()
        for handle in list(self._workers.values()):
            if handle.process.poll() is not None:
                continue
            try:
                self.repo.update_card_fields(
                    handle.claim.card_id,
                    {"last_heartbeat": now},
                    tenant_id=self.tenant_id,
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "heartbeat bump failed for %s: %s",
                    handle.claim.card_id, exc,
                )

    def _reap_workers(self, summary: dict[str, int]) -> None:
        done: list[str] = []
        for attempt_id, handle in self._workers.items():
            rc = handle.process.poll()
            if rc is None:
                # Wall-clock safety net. The real cost-cap path lands
                # in chunk 2b-ii; this generous default is the
                # last-resort backstop.
                age = time.monotonic() - handle.spawned_at
                if age > self.cfg.force_kill_after_seconds:
                    log.warning(
                        "force-killing worker attempt=%s after %.1fs",
                        attempt_id, age,
                    )
                    handle.process.kill_tree(grace_sec=2.0)
                    done.append(attempt_id)
                continue
            log.info(
                "worker exited attempt=%s card_id=%s rc=%d",
                attempt_id, handle.claim.card_id, rc,
            )
            done.append(attempt_id)
            summary["worker_exits"] += 1
            self._post_worker_exit(handle, rc)
        for attempt_id in done:
            self._workers.pop(attempt_id, None)

    def _post_worker_exit(self, handle: _WorkerHandle, rc: int) -> None:
        """Land a finished worker's results back into the store.

        The worker drove the projected card file: appended completion
        notes and stamped `finished_at` / `actual_tokens` /
        `model_used` / `cascade_history`. The runner parses that file
        and writes the executor-owned deltas (and only those) into the
        store, plus one `executed` event, then routes on the exit
        code:

        - **0 (clean):** the verifier runs. PASS -> `done`. FAIL ->
          back to `backlog` with `verifier_notes` written into the
          body. NEEDS_STANDUP_REVIEW -> `awaiting_standup_review`.
          Verifier internal error (after 2 retries) -> `blocked`.
          When `verifier_enabled=False` the card is left `active`,
          matching chunk 2 behavior.
        - **11 / 12 (cost-cap halt / cascade-exhausted halt):** the
          card is transitioned to `blocked` -- RUNNER_CONTRACT.md's
          "Cost cap enforcement" and "Cascade-on-confidence routing"
          both end an exhausted run in `blocked/` with the detail on
          the card.
        - **any other non-zero:** the card is left `active`; its
          heartbeat will go stale and orphan reclaim returns it to
          `backlog` for another attempt, exactly as in chunk 1.

        Every escalation the executor recorded in `cascade_history`
        this attempt is also emitted as an `escalated` event.
        """
        claim = handle.claim
        body_md: str | None = None
        fields: dict[str, Any] = {}
        worker_status: str | None = None
        if claim.card_file.is_file():
            try:
                parsed = parse_card_text(
                    claim.card_file.read_text(encoding="utf-8")
                )
                body_md = parsed.body_md
                fm = parsed.frontmatter
                # Capture the worker's view of `status` separately --
                # the contract reserves the `awaiting_amendment_review`
                # value as the executor-side amendment signal. We do not
                # promote it into `fields` because the daemon owns
                # status transitions.
                if "status" in fm and fm["status"] is not None:
                    worker_status = str(fm["status"])
                for key in (
                    "finished_at",
                    "last_heartbeat",
                    "actual_tokens",
                    "actual_duration_minutes",
                    "model_used",
                    "cascade_history",
                ):
                    if key in fm:
                        fields[key] = fm[key]
            except (ProjectionError, OSError) as exc:
                log.error(
                    "could not read back projected card %s: %s",
                    claim.card_file, exc,
                )
        else:
            log.warning(
                "projected card file %s missing after worker exit",
                claim.card_file,
            )

        sidecar = self._read_result_sidecar(claim.run_dir)
        payload: dict[str, Any] = {
            "exit_code": rc,
            "ok": rc == 0,
            "attempt_trace_id": claim.attempt_trace_id,
        }
        for key in ("halt_kind", "actual_cost_usd", "model_used",
                    "escalations", "actual_tokens"):
            if key in sidecar:
                payload[key] = sidecar[key]

        event = CardEvent(
            card_id=claim.card_id,
            tenant_id=self.tenant_id,
            type=EventType.EXECUTED.value,
            actor_id=claim.attempt_trace_id,
            actor_type=ActorType.EXECUTOR.value,
            at=now_utc_iso(),
            payload=payload,
        )
        try:
            self.repo.apply_executor_result(
                claim.card_id,
                tenant_id=self.tenant_id,
                body_md=body_md,
                fields=fields or None,
                event=event,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "failed to land worker result for %s: %s",
                claim.card_id, exc,
            )
            return

        self._emit_escalated_events(claim, fields.get("cascade_history"))
        self._record_executor_metrics(claim, fields, sidecar)

        if rc in (EXIT_COST_CAP_HALT, EXIT_HALT_SIGNAL):
            self._route_halt_to_blocked(claim, rc, sidecar)
        elif rc != 0:
            log.warning(
                "worker for card_id=%s exited non-zero (%d); card left "
                "active for orphan reclaim",
                claim.card_id, rc,
            )
        elif self._executor_requested_amendment(worker_status, body_md):
            # Executor wrote a change_request and stamped
            # status=awaiting_amendment_review. The contract REQUIRES
            # the runner to honor this rather than running the
            # verifier (RUNNER_CONTRACT.md "AC amendment protocol").
            self._route_to_amendments(claim, body_md=body_md, fields=fields)
        else:
            # Clean executor exit: dispatch the verifier.
            self._dispatch_verifier(claim, body_md=body_md, fields=fields, sidecar=sidecar)

    @staticmethod
    def _read_result_sidecar(run_dir: Path) -> dict[str, Any]:
        """Best-effort read of the worker's `result.json`.

        A missing or malformed sidecar is not an error -- the daemon
        falls back to the bare exit code. The stub worker writes a
        minimal sidecar; the SDK worker writes the full token / cost /
        cascade record.
        """
        path = run_dir / WORKER_RESULT_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _ledger_writer(self) -> "LedgerWriter | None":
        """Return the metrics writer, building it once on first use.

        None when `cfg.ledger_enabled` is False (the default) so the
        lifecycle hooks short-circuit with zero overhead. Construction
        failure is swallowed -- a broken metrics writer must not take
        the daemon down."""
        if not self.cfg.ledger_enabled:
            return None
        if self._ledger is None:
            try:
                store = MetricsStore.from_repository(self.repo)
                self._ledger = LedgerWriter(self.paths, store)
            except Exception as exc:  # noqa: BLE001 - best-effort.
                log.warning("could not build ledger writer: %s", exc)
                return None
        return self._ledger

    def _record_executor_metrics(
        self, claim: ClaimedCard, fields: dict[str, Any],
        sidecar: dict[str, Any],
    ) -> None:
        """Best-effort: record card-created + executor-exit metrics.

        Wholly guarded -- any failure logs at WARNING and returns. The
        metrics ledger is a denormalized side-record; it must never
        affect the executor-result landing that just succeeded."""
        writer = self._ledger_writer()
        if writer is None:
            return
        try:
            record = self.repo.get_card(claim.card_id, tenant_id=self.tenant_id)
            if record is None:
                return
            pin = record.field_value("pin_required")
            writer.record_card_created(
                card_id=claim.card_id,
                tenant_id=self.tenant_id,
                work_type=record.work_type,
                tier=record.points,
                pin_required=None if pin is None else bool(pin),
            )
            if record.started_at is not None:
                writer.record_card_started(
                    card_id=claim.card_id,
                    tenant_id=self.tenant_id,
                    attempt_trace_id=claim.attempt_trace_id,
                    started_at=record.started_at,
                )
            tokens_raw = fields.get("actual_tokens")
            if tokens_raw is None:
                tokens_raw = sidecar.get("actual_tokens")
            cost_raw = sidecar.get("actual_cost_usd")
            finished_raw = fields.get("finished_at")
            writer.record_executor_exit(
                card_id=claim.card_id,
                tenant_id=self.tenant_id,
                attempt_trace_id=claim.attempt_trace_id,
                started_at=record.started_at,
                finished_at=None if finished_raw is None else str(finished_raw),
                tokens=int(tokens_raw) if tokens_raw is not None else 0,
                cost_usd=float(cost_raw) if cost_raw is not None else None,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort by contract.
            log.warning(
                "ledger executor-metrics write failed for %s: %s",
                claim.card_id, exc,
            )

    def _record_verifier_metrics(
        self, claim: ClaimedCard, result: "VerifierResult | None"
    ) -> None:
        """Best-effort: record the verifier verdict to the ledger.

        A FAIL verdict also stamps one rework cycle (idempotent on the
        attempt). A skipped verifier (`result is None`) records nothing --
        no verification happened. Verifier token attribution is not yet
        surfaced by `verify_card`, so tokens are recorded as 0; the rework
        signal is the value here (it feeds the estimator's rework rate and
        the gate's historical floor)."""
        writer = self._ledger_writer()
        if writer is None or result is None:
            return
        try:
            writer.record_verifier_decided(
                card_id=claim.card_id,
                tenant_id=self.tenant_id,
                attempt_trace_id=claim.attempt_trace_id,
                failed=result.overall_status == VERDICT_FAIL,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort by contract.
            log.warning(
                "ledger verifier-metrics write failed for %s: %s",
                claim.card_id, exc,
            )

    def _record_merge_gate_metrics(
        self, claim: ClaimedCard, outcome: "MergeOutcome"
    ) -> None:
        """Best-effort: record the merge-gate decision (and PR-open, when
        a PR was opened) to the ledger. A skipped/no-op gate records
        nothing.

        Note: with `pr_gate_enabled=False` (the default) the gate degrades
        to `skipped=True`, so `merge_gate` stays null for cards that still
        land in `done` under chunk-3 auto-merge. That is by design here --
        the gate did not actually route a PR. When the PR gate is wired,
        the real `auto`/`sibling_review`/`human_review` decision is
        captured."""
        writer = self._ledger_writer()
        if writer is None or outcome.skipped:
            return
        try:
            writer.record_merge_gate(
                card_id=claim.card_id,
                tenant_id=self.tenant_id,
                gate=outcome.decision,
            )
            if outcome.pr_url:
                writer.record_pr_opened(
                    card_id=claim.card_id,
                    tenant_id=self.tenant_id,
                    pr_opened_at=now_utc_iso(),
                )
        except Exception as exc:  # noqa: BLE001 - best-effort by contract.
            log.warning(
                "ledger merge-gate-metrics write failed for %s: %s",
                claim.card_id, exc,
            )

    def _record_pr_merged_metrics(self, decision: "UnblockDecision") -> None:
        """Best-effort: record a PR merge to the ledger from an unblock
        decision. The writer derives `human_review_wall_seconds` from the
        pr-opened event (recorded by the merge-gate hook) and this
        merged_at. Diff stats come from the `gh pr view` the unblocker
        already ran."""
        writer = self._ledger_writer()
        if writer is None:
            return
        try:
            writer.record_pr_merged(
                card_id=decision.card_id,
                tenant_id=self.tenant_id,
                merged_at=decision.merged_at,
                diff_lines_added=decision.diff_lines_added,
                diff_lines_removed=decision.diff_lines_removed,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort by contract.
            log.warning(
                "ledger pr-merged write failed for %s: %s",
                decision.card_id, exc,
            )

    def _emit_escalated_events(
        self, claim: ClaimedCard, cascade_history: Any
    ) -> None:
        """Emit one `escalated` event per escalation made THIS attempt.

        `cascade_history` is append-only across a card's whole life,
        so the daemon filters to entries tagged with this attempt's
        `attempt_trace_id` -- a re-claimed card does not re-emit its
        earlier escalations.
        """
        if not isinstance(cascade_history, list):
            return
        for entry in cascade_history:
            if not isinstance(entry, dict):
                continue
            if entry.get("attempt_trace_id") != claim.attempt_trace_id:
                continue
            try:
                self.repo.append_event(
                    CardEvent(
                        card_id=claim.card_id,
                        tenant_id=self.tenant_id,
                        type=EventType.ESCALATED.value,
                        actor_id=claim.attempt_trace_id,
                        actor_type=ActorType.EXECUTOR.value,
                        at=str(entry.get("at") or now_utc_iso()),
                        payload=dict(entry),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "failed to append escalated event for %s: %s",
                    claim.card_id, exc,
                )

    @staticmethod
    def _executor_requested_amendment(
        worker_status: str | None, body_md: str | None
    ) -> bool:
        """Detect the executor's AC-amendment signal.

        Primary signal: the worker stamped `status:` in the projected
        card's frontmatter to `awaiting_amendment_review` (the long
        form the contract names) or `amendments` (the short form some
        executor implementations may emit). Secondary signal: a
        `change_request:` block in the body. Either is sufficient; we
        require the status field as the authoritative trigger so an
        executor that only annotated the body never accidentally
        skips verification.
        """
        if worker_status is None:
            return False
        value = worker_status.strip().lower()
        if value in {"awaiting_amendment_review", "amendments"}:
            return True
        # Fallback: the body has a change_request block. Surface the
        # mismatch so an executor that forgot to update status is
        # logged for human attention but still routed correctly.
        if body_md and "change_request:" in body_md and value not in {
            "active", "backlog", "done", "blocked",
            "awaiting_standup_review",
        }:
            log.warning(
                "executor wrote change_request but status=%r; routing "
                "to amendments anyway",
                worker_status,
            )
            return True
        return False

    def _route_to_amendments(
        self,
        claim: ClaimedCard,
        *,
        body_md: str | None,
        fields: dict[str, Any],
    ) -> None:
        """Move a card to the amendments status and notify the human path.

        Per RUNNER_CONTRACT.md "AC amendment protocol":

        - Atomic move (subfolder change only in v1; status column flip
          here in chunk 2b+).
        - Clear `claimed_by`, `started_at`, `last_heartbeat`,
          `attempt_trace_id`.
        - Branch is left alone -- partial work on the executor's branch
          is preserved.
        - Notify the human review path (a marker file at
          `signals/amendments/<card_id>.todo` is chunk 4's mechanism;
          the contract leaves the choice to the runner).
        - The runner MUST never amend AC on its own initiative; this
          method only routes, never edits the `acceptance_criteria:`
          block.
        """
        # Persist the worker's body (which carries the change_request
        # block the human reviewer needs to read) before transitioning.
        if body_md is not None:
            try:
                self.repo.apply_executor_result(
                    claim.card_id,
                    tenant_id=self.tenant_id,
                    body_md=body_md,
                    fields=fields or None,
                    event=None,
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "could not write amendment body for %s: %s",
                    claim.card_id, exc,
                )

        amend_fields: dict[str, Any] = {
            "claimed_by": None,
            "started_at": None,
            "last_heartbeat": None,
            "attempt_trace_id": None,
            # The merge_status field is left alone; an amendment-routed
            # card was never merged and the field still says "pending".
        }
        try:
            self.repo.transition(
                claim.card_id,
                to_status=CardStatus.AMENDMENTS.value,
                tenant_id=self.tenant_id,
                fields=amend_fields,
                actor_id=claim.attempt_trace_id,
                actor_type=ActorType.RUNNER.value,
                event_type=EventType.AMENDED.value,
                payload={
                    "reason": (
                        "executor wrote change_request and requested "
                        "amendment review"
                    ),
                    "attempt_trace_id": claim.attempt_trace_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "amendment transition failed for %s: %s",
                claim.card_id, exc,
            )
            return

        # Drop a marker for any human-review tooling watching the
        # signals dir. Best-effort; the canonical state is the card
        # row in the store.
        try:
            marker_dir = self.paths.signals / "amendments"
            marker_dir.mkdir(parents=True, exist_ok=True)
            (marker_dir / f"{claim.card_id}.todo").write_text(
                f"awaiting amendment review for {claim.card_id}\n"
                f"attempt: {claim.attempt_trace_id}\n"
                f"at: {now_utc_iso()}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning(
                "could not drop amendment marker for %s: %s",
                claim.card_id, exc,
            )

    def _route_halt_to_blocked(
        self, claim: ClaimedCard, rc: int, sidecar: dict[str, Any]
    ) -> None:
        """Transition a halted card to `blocked` with the halt detail."""
        if rc == EXIT_COST_CAP_HALT:
            reason = "executor halted on cost-cap breach"
        else:
            reason = (
                "executor cascade exhausted without reaching the "
                "confidence threshold"
            )
        log.warning("routing card_id=%s to blocked: %s", claim.card_id, reason)
        try:
            self.repo.transition(
                claim.card_id,
                to_status=CardStatus.BLOCKED.value,
                tenant_id=self.tenant_id,
                actor_id=claim.attempt_trace_id,
                actor_type=ActorType.EXECUTOR.value,
                event_type=EventType.BLOCKED.value,
                payload={
                    "exit_code": rc,
                    "reason": reason,
                    "halt_kind": sidecar.get("halt_kind"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "failed to route halted card %s to blocked: %s",
                claim.card_id, exc,
            )

    # ---- verifier dispatch (chunk 3) ---------------------------------

    def _dispatch_verifier(
        self,
        claim: ClaimedCard,
        *,
        body_md: str | None,
        fields: dict[str, Any],
        sidecar: dict[str, Any],
    ) -> None:
        """Run the cold-read verifier on a clean executor exit.

        Routes the card per the verifier's verdict (see
        `_post_worker_exit` docstring). When `verifier_enabled` is
        False the card is left `active`, exactly as chunk 2 left it.
        The verifier's own internal crash (`VerifierError`) is retried
        up to `_VERIFIER_MAX_RETRIES` times before the card routes to
        `blocked`.
        """
        if not self.cfg.verifier_enabled:
            log.info("verifier disabled; leaving %s active", claim.card_id)
            return

        record = self.repo.get_card(claim.card_id, tenant_id=self.tenant_id)
        if record is None:  # pragma: no cover - we just wrote it.
            log.error("card %s vanished before verifier dispatch", claim.card_id)
            return
        card_body = body_md if body_md is not None else record.body_md

        eligible_to_skip = self._verifier_skip_eligible(
            record, fields, sidecar
        )
        if eligible_to_skip:
            log.info(
                "verifier skip eligible for %s (high-confidence "
                "cascade-clean run); auto-passing to done",
                claim.card_id,
            )
            self._verifier_apply_pass(
                claim,
                result=None,
                skip_reason="high-confidence cascade-clean run",
            )
            return

        # Retry loop. Per the contract the orchestrator retries the
        # verifier; the verifier itself does not.
        result: VerifierResult | None = None
        last_error: VerifierError | None = None
        for attempt in range(_VERIFIER_MAX_RETRIES + 1):
            try:
                result = verify_card(
                    card_id=claim.card_id,
                    card_body=card_body,
                    worktree=claim.worktree_path,
                    env={},  # the daemon owns scrubbing; the verifier
                             # inherits an empty block, the contract's
                             # most defensive default.
                    subjective_client=self._build_subjective_client(),
                    subjective_starting_tier=self.cfg.subjective_starting_tier,
                    subjective_max_tier=self.cfg.subjective_max_tier,
                    subjective_confidence_threshold=
                        self.cfg.subjective_confidence_threshold,
                    subjective_disabled=self.cfg.verifier_cascade_disabled,
                )
                break
            except VerifierError as exc:
                last_error = exc
                log.warning(
                    "verifier crash on %s attempt %d/%d: %s",
                    claim.card_id, attempt + 1,
                    _VERIFIER_MAX_RETRIES + 1, exc,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = VerifierError(str(exc))
                log.exception(
                    "unexpected verifier exception on %s attempt %d/%d",
                    claim.card_id, attempt + 1, _VERIFIER_MAX_RETRIES + 1,
                )

        if result is None:
            self._verifier_route_error(claim, last_error)
            return

        self._record_verifier_metrics(claim, result)

        if result.overall_status == VERDICT_PASS:
            self._verifier_apply_pass(claim, result=result, skip_reason=None)
        elif result.overall_status == VERDICT_FAIL:
            self._verifier_apply_fail(claim, result)
        elif result.overall_status == VERDICT_STANDUP:
            self._verifier_apply_standup(claim, result)
        else:  # pragma: no cover - defensive.
            log.error(
                "verifier returned unknown verdict %r for %s; routing to blocked",
                result.overall_status, claim.card_id,
            )
            self._verifier_route_error(
                claim,
                VerifierError(f"unknown verdict {result.overall_status!r}"),
            )

    def _verifier_skip_eligible(
        self,
        record: CardRecord,
        fields: dict[str, Any],
        sidecar: dict[str, Any],
    ) -> bool:
        """RUNNER_CONTRACT.md "When the verifier MAY be skipped".

        All four conditions must hold. The cascade-history check looks
        at the merged history (record + fields-just-applied); a
        non-empty history disqualifies skip.
        """
        cascade = fields.get("cascade_history")
        if cascade is None:
            cascade = record.field_value("cascade_history")
        if isinstance(cascade, list) and len(cascade) > 0:
            return False
        # Skip requires high executor confidence; we read it from the
        # sidecar's `executor_confidence` slot when the executor wrote
        # one. Chunk 3's executor writes it via the sidecar's
        # `confidence` key when it is present; the absence of a value
        # is a vote AGAINST skipping (be conservative).
        conf = sidecar.get("executor_confidence")
        if conf is None:
            return False
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            return False
        if conf_f < self.cfg.verifier_skip_confidence_threshold:
            return False
        # The contract additionally requires no `type: subjective`
        # items and no AC retries. The verifier sees that with a quick
        # parse, so we delegate that check to a dry parse of the body.
        try:
            from ..verifier.parse import parse_acceptance_block

            items = parse_acceptance_block(record.body_md)
        except Exception:  # noqa: BLE001
            return False
        if any(it.subjective for it in items):
            return False
        return True

    def _verifier_apply_pass(
        self,
        claim: ClaimedCard,
        *,
        result: VerifierResult | None,
        skip_reason: str | None,
    ) -> None:
        """Stamp verifier provenance, then route through the merge gate.

        Chunk 4 changes the transition target: instead of immediately
        moving a passed card to `done`, the verifier provenance is
        written and then the merge gate decides the final transition.
        Tier 1-2 (non-pinned) auto-merge and land `done`; tier 3-6 or
        pinned cards open a PR and route to `blocked` with the
        appropriate `merge_status` so the operator (sibling reviewer or
        Drew) can finish the merge. RUNNER_CONTRACT.md "Merge gates"
        defines the routing; "Status transitions" defines `blocked` as
        "cards finished but unmerged, or paused on a dependency", which
        is exactly the state an awaiting-merge card lives in.

        When `pr_gate_enabled=False` (the chunk-3 default and every
        test that hasn't opted in) the merge gate is a no-op that
        returns the auto-merge outcome directly -- the card lands
        `done` with `merge_status=merged` just like chunk 3.
        """
        now = now_utc_iso()
        verifier_fields: dict[str, Any] = {
            "verified_at": now,
            "verified_by": (
                "runner-verifier" if skip_reason is None else None
            ),
            "verifier_skipped_reason": skip_reason,
        }
        if result is not None and result.cascade_history_appendix:
            verifier_fields["verifier_cascade_history"] = self._merge_appendix(
                claim, result.cascade_history_appendix
            )

        # Persist the verifier provenance up front. The merge gate may
        # take a while (gh push + create + merge) and we want the
        # verified-at stamp landed before the gate runs in case it
        # fails midway -- the card's verifier history should reflect
        # that the verifier itself succeeded.
        try:
            self.repo.update_card_fields(
                claim.card_id,
                verifier_fields,
                tenant_id=self.tenant_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "could not stamp verifier provenance on %s: %s",
                claim.card_id, exc,
            )
            return

        # Emit a `verified` event for the audit log. The transition
        # that follows (driven by the merge gate) emits `merged` or
        # another verifier-event depending on the outcome.
        verified_payload = self._verifier_payload(result, skip_reason=skip_reason)
        try:
            self.repo.append_event(
                CardEvent(
                    card_id=claim.card_id,
                    tenant_id=self.tenant_id,
                    type=EventType.VERIFIED.value,
                    actor_id=claim.attempt_trace_id,
                    actor_type=ActorType.VERIFIER.value,
                    at=now,
                    payload=verified_payload,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "could not append verified event for %s: %s",
                claim.card_id, exc,
            )

        record = self.repo.get_card(claim.card_id, tenant_id=self.tenant_id)
        if record is None:  # pragma: no cover - just updated it.
            log.error("card %s vanished before merge gate", claim.card_id)
            return

        outcome = self._merge_gate.apply(
            claim, record, verified_at=now, project_config=self.project_config
        )
        self._record_merge_gate_metrics(claim, outcome)
        self._apply_merge_outcome(
            claim,
            outcome,
            verifier_result=result,
            skip_reason=skip_reason,
        )

    def _apply_merge_outcome(
        self,
        claim: ClaimedCard,
        outcome: MergeOutcome,
        *,
        verifier_result: VerifierResult | None,
        skip_reason: str | None,
    ) -> None:
        """Land the merge gate's decision as a card transition + event."""
        fields: dict[str, Any] = {"merge_status": outcome.merge_status}
        if outcome.pr_url:
            # Chunk 5: promote the PR URL to a queryable column on the
            # card row so the dashboard and the unblocker can read it
            # without grepping the event log.
            fields["pr_url"] = outcome.pr_url
        # Clear the claim provenance once the card leaves `active`. The
        # next reclaim (or future re-attempt after a blocked-on-merge
        # card unblocks) should be clean.
        if outcome.to_status != CardStatus.ACTIVE.value:
            fields.update({
                "claimed_by": None,
                "started_at": None,
                "last_heartbeat": None,
                "attempt_trace_id": None,
            })

        payload = self._verifier_payload(verifier_result, skip_reason=skip_reason)
        payload["merge_decision"] = outcome.decision
        payload["merge_status"] = outcome.merge_status
        payload["merge_reason"] = outcome.reason
        if outcome.pr_url:
            payload["pr_url"] = outcome.pr_url
        if outcome.extra_payload:
            payload.update(outcome.extra_payload)

        event_type = (
            EventType.MERGED.value
            if outcome.to_status == CardStatus.DONE.value
            else EventType.VERIFIED.value
        )
        try:
            self.repo.transition(
                claim.card_id,
                to_status=outcome.to_status,
                tenant_id=self.tenant_id,
                fields=fields,
                actor_id=claim.attempt_trace_id,
                actor_type=ActorType.RUNNER.value,
                event_type=event_type,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "merge-gate transition failed for %s -> %s: %s",
                claim.card_id, outcome.to_status, exc,
            )

    def _verifier_apply_fail(
        self, claim: ClaimedCard, result: VerifierResult
    ) -> None:
        """A failing verifier returns the card to backlog with notes.

        Per RUNNER_CONTRACT.md "Result shape": "append a
        `verifier_notes:` block to the card body ... move the card
        back to `active/`, and clear `claimed_by`, `started_at`,
        `last_heartbeat`. The next claim picks it up with the
        verifier's notes in hand."

        After the chunk 2b cutover `backlog` is the queryable bucket
        the daemon claims from, so the card goes to `backlog` (status
        column), not back into a folder. This matches the contract's
        intent: the next claim picks it up.
        """
        body_md = self._append_verifier_notes_block(claim, result)
        cascade_field = self._merge_appendix(claim, result.cascade_history_appendix)
        fields: dict[str, Any] = {
            "verifier_skipped_reason": None,
            "verifier_cascade_history": cascade_field,
            # Clear the claim provenance so the next claim is clean.
            "claimed_by": None,
            "started_at": None,
            "last_heartbeat": None,
            "attempt_trace_id": None,
        }
        if body_md is not None:
            try:
                self.repo.apply_executor_result(
                    claim.card_id,
                    tenant_id=self.tenant_id,
                    body_md=body_md,
                    fields=None,
                    event=None,
                )
            except Exception as exc:  # noqa: BLE001
                log.error("could not write verifier_notes for %s: %s", claim.card_id, exc)
        payload = self._verifier_payload(result, skip_reason=None)
        try:
            self.repo.transition(
                claim.card_id,
                to_status=CardStatus.BACKLOG.value,
                tenant_id=self.tenant_id,
                fields=fields,
                actor_id=claim.attempt_trace_id,
                actor_type=ActorType.VERIFIER.value,
                event_type=EventType.VERIFIED.value,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("verifier FAIL transition failed for %s: %s", claim.card_id, exc)

    def _verifier_apply_standup(
        self, claim: ClaimedCard, result: VerifierResult
    ) -> None:
        cascade_field = self._merge_appendix(claim, result.cascade_history_appendix)
        standup_reason = "; ".join(result.standup_reasons) or (
            "subjective AC items exhausted cascade without reaching threshold"
        )
        fields: dict[str, Any] = {
            "verifier_cascade_history": cascade_field,
            "standup_reason": standup_reason,
        }
        payload = self._verifier_payload(result, skip_reason=None)
        try:
            self.repo.transition(
                claim.card_id,
                to_status=CardStatus.AWAITING_STANDUP_REVIEW.value,
                tenant_id=self.tenant_id,
                fields=fields,
                actor_id=claim.attempt_trace_id,
                actor_type=ActorType.VERIFIER.value,
                event_type=EventType.VERIFIED.value,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "verifier STANDUP transition failed for %s: %s",
                claim.card_id, exc,
            )

    def _verifier_route_error(
        self, claim: ClaimedCard, error: VerifierError | None
    ) -> None:
        msg = str(error) if error is not None else "verifier failed silently"
        log.error(
            "verifier crashed twice on %s; routing to blocked: %s",
            claim.card_id, msg,
        )
        try:
            self.repo.transition(
                claim.card_id,
                to_status=CardStatus.BLOCKED.value,
                tenant_id=self.tenant_id,
                actor_id=claim.attempt_trace_id,
                actor_type=ActorType.VERIFIER.value,
                event_type=EventType.BLOCKED.value,
                payload={
                    "reason": "verifier crash exhausted retries",
                    "verifier_error": msg,
                    "max_retries": _VERIFIER_MAX_RETRIES,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "could not route %s to blocked after verifier crash: %s",
                claim.card_id, exc,
            )

    def _build_subjective_client(self) -> Any | None:
        """Return an Anthropic client for the subjective cascade, or None.

        The daemon only constructs the client when ANTHROPIC_API_KEY is
        in its own env block. A card with no subjective items never
        uses the client, so returning None when the key is missing is
        safe; what fails (correctly) is a card carrying subjective
        items on a daemon configured without a key, which routes to
        standup review per RUNNER_CONTRACT.md.
        """
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        try:
            import anthropic
        except ImportError:
            log.warning(
                "subjective verifier needs `anthropic` but it is not "
                "installed; subjective items will route to standup review"
            )
            return None
        try:
            return anthropic.Anthropic(api_key=key)
        except Exception:  # noqa: BLE001
            log.exception("could not construct anthropic client for verifier")
            return None

    def _merge_appendix(
        self, claim: ClaimedCard, appendix: tuple[dict[str, Any], ...]
    ) -> list[dict[str, Any]]:
        """Append-only merge into the card's `verifier_cascade_history`."""
        record = self.repo.get_card(claim.card_id, tenant_id=self.tenant_id)
        existing = (
            record.field_value("verifier_cascade_history") if record else None
        )
        if not isinstance(existing, list):
            existing = []
        return list(existing) + [dict(e) for e in appendix]

    def _append_verifier_notes_block(
        self, claim: ClaimedCard, result: VerifierResult
    ) -> str | None:
        """Compose a `verifier_notes:` YAML block under the card body."""
        record = self.repo.get_card(claim.card_id, tenant_id=self.tenant_id)
        if record is None:
            return None
        notes_lines: list[str] = ["", "## Verifier notes", "", "```yaml", "verifier_notes:"]
        for item in result.items:
            verdict = "pass" if item.handler_result.passed else "fail"
            descr = item.item.get("description") or f"AC#{item.item_idx}"
            notes_lines.append(f"  - index: {item.item_idx}")
            notes_lines.append(f"    description: {descr!r}")
            notes_lines.append(f"    phase: {item.phase}")
            notes_lines.append(f"    result: {verdict}")
            evidence = item.handler_result.evidence or {}
            notes_lines.append(f"    evidence: {json.dumps(evidence, sort_keys=True)}")
        notes_lines.append("```")
        notes_lines.append("")
        notes_block = "\n".join(notes_lines)
        body = (record.body_md or "").rstrip() + "\n" + notes_block + "\n"
        return body

    @staticmethod
    def _verifier_payload(
        result: VerifierResult | None, *, skip_reason: str | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if skip_reason is not None:
            payload["skipped"] = True
            payload["skip_reason"] = skip_reason
        if result is not None:
            payload.update(
                overall_status=result.overall_status,
                items_total=len(result.items),
                items_failed=len(result.failed_items),
                standup_reason_items=list(result.standup_reason_items),
            )
        return payload

    def _drain_workers(self) -> None:
        if not self._workers:
            return
        log.info("draining %d worker(s)", len(self._workers))
        deadline = time.monotonic() + 30.0
        while self._workers and time.monotonic() < deadline:
            self._reap_workers({"worker_exits": 0})
            time.sleep(0.5)
        for attempt_id, handle in list(self._workers.items()):
            log.warning(
                "force-killing worker attempt=%s during shutdown",
                attempt_id,
            )
            handle.process.kill_tree(grace_sec=2.0)
            self._workers.pop(attempt_id, None)

    def _find_handle_for_card(self, card_id: str) -> _WorkerHandle | None:
        for handle in self._workers.values():
            if handle.claim.card_id == card_id:
                return handle
        return None

    # ---- chunk 5: sibling-reviewer client builder --------------------

    def _build_sibling_reviewer_client(self) -> SiblingReviewerClient:
        """Return the live sibling-reviewer client.

        Tests inject an explicit client via the constructor; production
        defaults to the Anthropic-backed implementation when
        ANTHROPIC_API_KEY is present, otherwise a static one whose
        decisions are always `comment` (so the marker still lands but
        no auto-merge fires).
        """
        if self._sibling_reviewer_client is not None:
            return self._sibling_reviewer_client
        client = self._build_subjective_client()
        if client is not None:
            return AnthropicSiblingReviewerClient(client=client)
        log.info(
            "no Anthropic client available; sibling reviewer will use the "
            "static no-opinion fallback"
        )
        return StaticSiblingReviewerClient()

    def _build_amendment_reviewer_client(self) -> SiblingReviewerClient:
        """Mirror of `_build_sibling_reviewer_client` for the amendments
        sweep -- the AC amendment reviewer is structurally the same kind
        of small LLM call against a card + amendment payload."""
        if self._amendment_reviewer_client is not None:
            return self._amendment_reviewer_client
        client = self._build_subjective_client()
        if client is not None:
            return AnthropicSiblingReviewerClient(client=client)
        log.info(
            "no Anthropic client available; amendment reviewer will use "
            "the static no-opinion fallback"
        )
        return StaticSiblingReviewerClient()

    def _build_amendment_editor_client(self) -> AmendmentEditorClient | None:
        """Return the structured-output editor for `auto_edit_ac` mode.

        Chunk 6a: the editor only runs when the project opted in
        (`amendment_reviewer.auto_edit_ac: true`). We still construct
        the client unconditionally when an Anthropic SDK is available
        so a tick that finds an opted-in card has a client to call;
        the reviewer itself decides whether to invoke it. Returns
        None when no Anthropic client could be built (tests inject
        their own static client).
        """
        if self._amendment_editor_client is not None:
            return self._amendment_editor_client
        client = self._build_subjective_client()
        if client is None:
            return None
        return AnthropicAmendmentEditorClient(client=client)

    # ---- chunk 5: git worktree prune sweep ---------------------------

    def _maybe_prune_git_worktrees(self) -> None:
        """Run `git worktree prune` per project once the interval elapses.

        The runner does not (today) track the set of project repos
        explicitly; we derive it from the distinct `project` fields on
        live cards. A project with zero cards in the store contributes
        nothing -- which is correct, the runner has nothing to clean up
        there.
        """
        if self.cfg.skip_worktree:
            return  # tests run against tmp dirs, never against a real git repo.
        if self.cfg.worktree_prune_interval_sec <= 0:
            return
        now = time.monotonic()
        if self._last_prune_at and now - self._last_prune_at < (
            self.cfg.worktree_prune_interval_sec
        ):
            return
        seen: set[Path] = set()
        for record in self.repo.query_cards(tenant_id=self.tenant_id):
            project = record.project
            if not project:
                continue
            try:
                project_dir = Path(project).resolve()
            except OSError:
                continue
            if project_dir in seen:
                continue
            seen.add(project_dir)
            try:
                prune_git_worktrees(project_dir=project_dir)
            except Exception:  # noqa: BLE001 - defensive sweep.
                log.exception(
                    "worktree prune sweep failed for %s; continuing",
                    project_dir,
                )
        self._last_prune_at = now
