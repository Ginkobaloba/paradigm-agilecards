"""The daemon main loop.

Responsibilities:

- Singleton enforcement via `.daemon.lock` (file + PID).
- Boot sanity: atomic-rename sentinel; reconcile `active/` cards.
- Polling loop: scan backlog/, claim eligible cards, spawn stub
  workers, watch `active/` for stale heartbeats and dead workers.
- Clean shutdown: receive a stop signal, tell workers to wrap up,
  release the singleton lock.

Per the cards-are-state principle the daemon holds NO durable state
of its own. The map of `attempt_trace_id -> ManagedProcess` is an
in-memory cache only; if the daemon dies, the next boot finds active
cards on disk and treats anything with a fresh heartbeat as still
alive (the worker outlived us; do not respawn).
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Optional

from ..common.card_io import parse_card_file, scan_card_dir, write_card_file
from ..common.locks import FileLock, LockHeldError, pid_alive
from ..common.logging_setup import setup_daemon_logging
from ..common.process_group import ManagedProcess
from ..common.types import (
    ClaimedCard,
    DaemonConfig,
    RuntimePaths,
    now_utc_iso,
)
from .atomic_rename_sentinel import ensure_sentinel_or_force_serial
from .claim import ClaimRace, attempt_claim
from .orphan import reclaim, scan_for_orphans
from .spawner import spawn_worker
from .worktree import WorktreeCreateError, prepare_worktree


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

    Single instance per host. Constructed with a `DaemonConfig`.
    `run()` blocks until `stop()` is called or a SIGTERM-style signal
    arrives.
    """

    def __init__(self, cfg: DaemonConfig) -> None:
        self.cfg = cfg
        self.paths = RuntimePaths.from_root(cfg.todo_root)
        self.paths.ensure()
        self._workers: dict[str, _WorkerHandle] = {}
        self._stop_event = threading.Event()
        self._singleton_lock: FileLock | None = None
        self._effective_max_parallel = cfg.max_parallel
        self._last_tick_at: float = 0.0
        self._last_tick_summary: dict[str, int] = {}

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
        return 0

    def stop(self) -> None:
        """Signal the polling loop to exit.

        Safe to call from a signal handler.
        """
        log.info("stop requested; signaling loop")
        self._stop_event.set()

    @property
    def effective_max_parallel(self) -> int:
        return self._effective_max_parallel

    @property
    def last_tick_summary(self) -> dict[str, int]:
        return dict(self._last_tick_summary)

    @property
    def last_tick_at(self) -> float:
        return self._last_tick_at

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
        self._effective_max_parallel = ensure_sentinel_or_force_serial(
            self.paths, max_parallel=self.cfg.max_parallel
        )
        if self._effective_max_parallel != self.cfg.max_parallel:
            log.warning(
                "max_parallel reduced %d -> %d",
                self.cfg.max_parallel, self._effective_max_parallel,
            )

        # Reconcile any cards in active/ from a prior daemon. Per
        # design section 9 we trust the filesystem: if a card has a
        # fresh heartbeat we assume a surviving worker still owns it,
        # so we do NOT respawn. If the heartbeat is stale we orphan-
        # reclaim it.
        orphans = scan_for_orphans(paths=self.paths, cfg=self.cfg)
        for card_path in orphans:
            try:
                reclaim(card_path, paths=self.paths)
            except Exception as exc:
                log.error("reclaim failed for %s: %s", card_path, exc)

        # Repair any malformed-claim cards: in active/ but missing
        # claimed_by. This is the "daemon killed between move and
        # stamp" window. Re-stamp them so the next pass treats them
        # like a fresh claim. We do not spawn a worker for them; we
        # let the orphan loop pick them up.
        for entry in self.paths.active.iterdir():
            if not entry.is_file() or entry.suffix != ".md":
                continue
            try:
                snap = parse_card_file(entry)
            except Exception:
                continue
            if snap.get("claimed_by") is None and snap.get("attempt_trace_id") is None:
                now = now_utc_iso()
                snap.frontmatter["claimed_by"] = "daemon-boot-reconcile"
                snap.frontmatter["started_at"] = now
                snap.frontmatter["last_heartbeat"] = now
                snap.frontmatter["status"] = "active"
                write_card_file(entry, snap)
                log.info("repaired malformed-claim card %s", entry.name)

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
        }
        # 1. Reap exited workers first; this frees parallelism slots.
        self._reap_workers(summary)

        # 2. Orphan scan.
        orphans = scan_for_orphans(paths=self.paths, cfg=self.cfg)
        for card_path in orphans:
            handle = self._find_handle_for_card_path(card_path)
            if handle is not None:
                # We still have a process. Kill it before reclaiming.
                log.warning(
                    "killing stale worker for %s before reclaim",
                    card_path.name,
                )
                handle.process.kill_tree(grace_sec=2.0)
                self._workers.pop(handle.claim.attempt_trace_id, None)
            try:
                reclaim(card_path, paths=self.paths)
                summary["orphans_reclaimed"] += 1
            except Exception as exc:
                log.error("reclaim failed for %s: %s", card_path, exc)

        # 3. Claim new cards up to capacity.
        summary["active_before"] = self._count_active()
        capacity = self._effective_max_parallel - len(self._workers)
        for card_path in scan_card_dir(self.paths.backlog):
            if capacity <= 0:
                break
            if not self._can_claim(card_path):
                continue
            claim = self._try_claim(card_path)
            if claim is None:
                continue
            if not self._prepare_and_spawn(claim):
                continue
            summary["claims"] += 1
            capacity -= 1

        self._last_tick_summary = summary

    def _can_claim(self, card_path: Path) -> bool:
        """Eligibility check (chunk 1 keeps it simple).

        Chunk 1 ignores `depends_on` and `requires_pre_approval`; those
        ship in chunks 3 and 4. We only check that the card has a
        parseable frontmatter and is not already in flight.
        """
        try:
            snap = parse_card_file(card_path)
        except Exception as exc:
            log.warning("could not parse %s: %s", card_path, exc)
            return False
        if snap.get("status") not in (None, "backlog"):
            return False
        return True

    def _try_claim(self, card_path: Path) -> ClaimedCard | None:
        try:
            return attempt_claim(
                card_path,
                paths=self.paths,
                claimed_by=f"cards-runner-daemon@pid{os.getpid()}",
            )
        except ClaimRace as exc:
            log.debug("claim race lost on %s: %s", card_path.name, exc)
            return None
        except Exception:
            log.exception("unexpected error claiming %s", card_path.name)
            return None

    def _prepare_and_spawn(self, claim: ClaimedCard) -> bool:
        run_dir = self.paths.runs / claim.attempt_trace_id
        run_dir.mkdir(parents=True, exist_ok=True)
        project = claim.snapshot.get("project")
        branch = claim.snapshot.get("branch", f"card/{claim.card_id}")
        base = claim.snapshot.get("base_branch", "main")
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

        try:
            process = spawn_worker(
                cfg=self.cfg,
                claim=claim,
                run_dir=run_dir,
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
        """Move a half-prepared claim back to backlog/."""
        try:
            reclaim(claim.active_path, paths=self.paths)
        except Exception:
            log.exception("rollback failed for %s", claim.card_id)

    def _reap_workers(self, summary: dict[str, int]) -> None:
        done: list[str] = []
        for attempt_id, handle in self._workers.items():
            rc = handle.process.poll()
            if rc is None:
                # Wall-clock safety net: chunk 1 default is generous;
                # chunk 2 brings the real cost-cap path.
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
        """Chunk 1: stub workers update card frontmatter themselves
        and exit 0 on success. The daemon does not yet dispatch to
        the verifier (chunk 3) or merge orchestration (chunk 3-4).
        We simply observe the exit.

        On non-zero exit we leave the card in `active/`; orphan reclaim
        will pick it up after the heartbeat goes stale. Chunk 2 wires
        proper exit-code routing.
        """
        if rc != 0:
            log.warning(
                "worker for card_id=%s exited non-zero (%d); "
                "leaving card in active/ for orphan reclaim",
                handle.claim.card_id, rc,
            )

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

    def _find_handle_for_card_path(self, card_path: Path) -> _WorkerHandle | None:
        for handle in self._workers.values():
            if handle.claim.active_path.name == card_path.name:
                return handle
        return None

    def _count_active(self) -> int:
        if not self.paths.active.is_dir():
            return 0
        return sum(
            1 for p in self.paths.active.iterdir()
            if p.is_file() and p.suffix == ".md"
        )
