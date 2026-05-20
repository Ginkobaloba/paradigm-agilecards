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
from ..store.projection import ProjectionError, parse_card_text, project_card_file
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

    Single instance per host. Constructed with a `DaemonConfig`. An
    already-open `CardRepository` may be injected (tests do this so
    the daemon shares the test's connection); otherwise the daemon
    opens its own from `cfg.resolved_store_spec()` at boot and owns
    its lifecycle. `run()` blocks until `stop()` is called or a
    SIGTERM-style signal arrives.
    """

    def __init__(
        self, cfg: DaemonConfig, *, repo: CardRepository | None = None
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
        """Claim eligibility. The chunk 2b cutover keeps this simple.

        `query_cards(status="backlog")` already filters to backlog
        cards. Dependency gating (`depends_on`), story-drift checks,
        and the pre-approval gate are chunks 3-4 and will read from
        the store's `dependencies` table and signal markers. For now
        every backlog card is eligible.
        """
        del record
        return True

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

        - **0 (clean):** the card is left `active`. A clean executor
          finish is not itself a transition to `done`; the verifier
          owns that arrow (chunk 3), and picks the card up from
          `active`.
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
        if claim.card_file.is_file():
            try:
                parsed = parse_card_text(
                    claim.card_file.read_text(encoding="utf-8")
                )
                body_md = parsed.body_md
                fm = parsed.frontmatter
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

        if rc in (EXIT_COST_CAP_HALT, EXIT_HALT_SIGNAL):
            self._route_halt_to_blocked(claim, rc, sidecar)
        elif rc != 0:
            log.warning(
                "worker for card_id=%s exited non-zero (%d); card left "
                "active for orphan reclaim",
                claim.card_id, rc,
            )

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
