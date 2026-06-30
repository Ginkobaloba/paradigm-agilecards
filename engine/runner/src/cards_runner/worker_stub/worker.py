"""Worker entry point used by the daemon's spawner.

The worker reads and writes the per-run projected card file the
daemon wrote into the run dir (CARDS_RUNNER_CARD_PATH). That file is
an ephemeral per-run view; on worker exit the daemon parses it back
into the canonical card store. The worker never touches the store
directly -- it works a Markdown file exactly as a v1 worker did.

Lifecycle:

1. Read CARDS_RUNNER_CARD_PATH and CARDS_RUNNER_WORKTREE from env.
2. Parse the projected card frontmatter.
3. Start a heartbeat thread that touches `<worktree>/.cards-heartbeat`
   and advances the projected card's `last_heartbeat` field.
4. Call the selected `Invoker`. `CARDS_RUNNER_INVOKER` picks it:
   `stub` (chunk 1 `StubInvoker`, zero tokens) or `sdk` (chunk 2b-ii
   `SdkInvoker`, the real Anthropic-SDK-in-process executor).
5. Stamp the projected card (completion notes, `finished_at`,
   `actual_tokens`, `actual_duration_minutes`, `model_used`, and any
   `cascade_history` the executor produced) and write a structured
   `result.json` sidecar the daemon reads back.
6. Return an exit code the daemon routes on: 0 clean, 10 invoker
   error, 11 cost-cap halt, 12 cascade-exhausted halt.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..common.atomic import atomic_touch
from ..common.card_io import (
    append_completion_notes,
    parse_card_file,
    write_card_file,
)
from ..common.logging_setup import setup_worker_logging
from ..common.types import (
    EXIT_CLEAN,
    EXIT_COST_CAP_HALT,
    EXIT_HALT_SIGNAL,
    EXIT_STUB_ERROR,
    EXIT_UNCAUGHT,
    HEARTBEAT_FILE,
    WORKER_RESULT_NAME,
    now_utc_iso,
)
from .invoker import InvokeRequest, InvokeResult, Invoker, StubInvoker


log = logging.getLogger(__name__)


class _Heartbeat:
    """Background thread that writes the heartbeat file and frontmatter.

    Stops when `cancel()` is called. Designed to be cheap: tempfile-
    rename touch on the heartbeat file, full card rewrite for the
    frontmatter (which we accept because the cadence is low).
    """

    def __init__(
        self,
        *,
        card_path: Path,
        worktree: Path,
        interval_sec: float,
        frontmatter_every_n: int = 1,
    ) -> None:
        self.card_path = card_path
        self.worktree = worktree
        self.interval_sec = interval_sec
        self.frontmatter_every_n = max(1, frontmatter_every_n)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._tick = 0

    def start(self) -> None:
        # Always write at least one heartbeat synchronously so the
        # daemon's first poll after spawn sees fresh evidence.
        self._beat()
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_sec):
            try:
                self._beat()
            except Exception:  # noqa: BLE001
                log.exception("heartbeat tick failed")

    def _beat(self) -> None:
        self._tick += 1
        hb_path = self.worktree / HEARTBEAT_FILE
        atomic_touch(hb_path)
        if self._tick % self.frontmatter_every_n == 0:
            self._update_card_heartbeat()

    def _update_card_heartbeat(self) -> None:
        try:
            snap = parse_card_file(self.card_path)
        except FileNotFoundError:
            # Daemon may have reclaimed the card during a long beat
            # cycle. Stop trying.
            self._stop.set()
            return
        snap.frontmatter["last_heartbeat"] = now_utc_iso()
        try:
            write_card_file(self.card_path, snap)
        except FileNotFoundError:
            self._stop.set()


def _exit_code_for(result: InvokeResult) -> int:
    """Map an `InvokeResult` to the process exit code the daemon routes.

    A cost-cap or cascade-exhausted halt routes the card to `blocked`
    in `daemon._post_worker_exit`; a plain failure leaves the card
    `active` for orphan reclaim, exactly as a stub error did in
    chunk 1.
    """
    if result.halt_kind == "cost_cap":
        return EXIT_COST_CAP_HALT
    if result.halt_kind == "cascade_exhausted":
        return EXIT_HALT_SIGNAL
    return EXIT_CLEAN if result.success else EXIT_STUB_ERROR


def run_worker(
    *,
    card_path: Path,
    worktree: Path,
    attempt_trace_id: str,
    trace_id: str,
    heartbeat_interval_sec: float,
    invoker: Invoker,
) -> int:
    """Run one worker lifecycle. Returns the process exit code."""
    run_dir = card_path.parent
    snap = parse_card_file(card_path)
    request = InvokeRequest(
        snapshot=snap,
        worktree=worktree,
        attempt_trace_id=attempt_trace_id,
        trace_id=trace_id,
    )
    heartbeat = _Heartbeat(
        card_path=card_path,
        worktree=worktree,
        interval_sec=heartbeat_interval_sec,
    )
    heartbeat.start()
    started_at_mono = time.monotonic()
    try:
        result = invoker.invoke(request)
    except Exception:  # noqa: BLE001
        log.exception("invoker raised; writing error completion notes")
        heartbeat.cancel()
        _stamp_error(card_path, attempt_trace_id)
        _write_result_sidecar(run_dir, attempt_trace_id, EXIT_STUB_ERROR, None)
        return EXIT_STUB_ERROR
    heartbeat.cancel()

    duration_sec = time.monotonic() - started_at_mono
    rc = _exit_code_for(result)
    log.info(
        "worker invoker returned success=%s halt=%s rc=%d duration_sec=%.1f "
        "tokens=%d cost=$%.4f model=%s",
        result.success, result.halt_kind, rc, duration_sec,
        result.actual_tokens, result.actual_cost_usd, result.model_used,
    )
    _stamp_result(
        card_path=card_path,
        result=result,
        attempt_trace_id=attempt_trace_id,
        duration_sec=duration_sec,
    )
    _write_result_sidecar(run_dir, attempt_trace_id, rc, result)
    return rc


def _stamp_result(
    *,
    card_path: Path,
    result: InvokeResult,
    attempt_trace_id: str,
    duration_sec: float,
) -> None:
    """Write the executor's deltas back into the projected card file.

    Covers a clean finish and a halt alike: a halted run still wrote
    real partial work and a real token spend, and the card carries
    that record into `blocked` for the next human or executor.
    """
    try:
        snap = parse_card_file(card_path)
    except FileNotFoundError:
        log.warning(
            "card %s vanished before result stamp (probably reclaimed)",
            card_path,
        )
        return
    now_iso = now_utc_iso()
    snap.frontmatter["finished_at"] = now_iso
    snap.frontmatter["last_heartbeat"] = now_iso
    if result.model_used is not None:
        snap.frontmatter["model_used"] = result.model_used
    # The projected card file is rewritten whole now, so the worker
    # can set these fields directly -- no allowlist, no no-op for
    # fields the planner did not pre-bake.
    snap.frontmatter["actual_tokens"] = result.actual_tokens
    snap.frontmatter["actual_duration_minutes"] = round(duration_sec / 60.0, 2)
    snap.frontmatter["attempt_trace_id"] = attempt_trace_id
    if result.cascade_history:
        existing = snap.frontmatter.get("cascade_history")
        if not isinstance(existing, list):
            existing = []
        # Append-only across the card's whole lifetime, per
        # RUNNER_CONTRACT.md "Cascade-on-confidence routing".
        snap.frontmatter["cascade_history"] = existing + [
            dict(entry) for entry in result.cascade_history
        ]
    append_completion_notes(snap, result.completion_notes_markdown)
    write_card_file(card_path, snap)
    log.info("stamped result on %s", card_path)


def _stamp_error(card_path: Path, attempt_trace_id: str) -> None:
    try:
        snap = parse_card_file(card_path)
    except FileNotFoundError:
        return
    now_iso = now_utc_iso()
    snap.frontmatter["last_heartbeat"] = now_iso
    snap.frontmatter["attempt_trace_id"] = attempt_trace_id
    append_completion_notes(
        snap,
        "The executor raised an unexpected exception before it could "
        "report a result.\n"
        "See `_runs/<attempt_trace_id>/worker.log` for the full trace.\n",
    )
    try:
        write_card_file(card_path, snap)
    except FileNotFoundError:
        return


def _write_result_sidecar(
    run_dir: Path,
    attempt_trace_id: str,
    rc: int,
    result: InvokeResult | None,
) -> None:
    """Write the structured `result.json` the daemon reads on reap.

    Best-effort: a failure here must not change the worker's exit
    code, so the daemon simply sees a missing sidecar and falls back
    to the exit code alone.
    """
    payload: dict[str, object] = {
        "attempt_trace_id": attempt_trace_id,
        "exit_code": rc,
    }
    if result is not None:
        payload.update(
            success=result.success,
            halt_kind=result.halt_kind,
            actual_tokens=result.actual_tokens,
            actual_cost_usd=result.actual_cost_usd,
            model_used=result.model_used,
            escalations=len(result.cascade_history),
            cost=result.cost_snapshot,
        )
    else:
        payload["success"] = False
        payload["halt_kind"] = None
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / WORKER_RESULT_NAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("could not write result sidecar: %s", exc)


def _build_invoker() -> Invoker:
    """Select the executor from `CARDS_RUNNER_INVOKER` (default: stub)."""
    kind = os.environ.get("CARDS_RUNNER_INVOKER", "stub").strip().lower()
    if kind == "sdk":
        from .sdk_invoker import SdkInvoker

        log.info("using SdkInvoker (chunk 2b-ii real executor)")
        return SdkInvoker.from_env()
    stub_sleep_sec = float(os.environ.get("CARDS_RUNNER_STUB_SLEEP_SEC", "3"))
    log.info("using StubInvoker (sleep=%.1fs)", stub_sleep_sec)
    return StubInvoker(sleep_sec=stub_sleep_sec)


def main_from_env() -> int:
    """Default entry. Pulls paths and ids from env, builds the invoker."""
    card_path = Path(os.environ["CARDS_RUNNER_CARD_PATH"])
    worktree = Path(os.environ["CARDS_RUNNER_WORKTREE"])
    attempt_trace_id = os.environ["CARDS_RUNNER_ATTEMPT_TRACE_ID"]
    trace_id = os.environ.get("CARDS_RUNNER_TRACE_ID", attempt_trace_id)
    heartbeat_interval_sec = float(
        os.environ.get("CARDS_RUNNER_HEARTBEAT_INTERVAL_SEC", "30")
    )
    run_dir = Path(
        os.environ.get(
            "CARDS_RUNNER_RUN_DIR",
            str(worktree.parent),
        )
    )
    setup_worker_logging(run_dir)

    invoker = _build_invoker()
    log.info(
        "worker boot card=%s attempt=%s hb=%.1fs invoker=%s",
        card_path, attempt_trace_id, heartbeat_interval_sec,
        type(invoker).__name__,
    )
    try:
        return run_worker(
            card_path=card_path,
            worktree=worktree,
            attempt_trace_id=attempt_trace_id,
            trace_id=trace_id,
            heartbeat_interval_sec=heartbeat_interval_sec,
            invoker=invoker,
        )
    except Exception:
        log.exception("worker outer loop crashed")
        return EXIT_UNCAUGHT


# Keep import-time side effects minimal so tests that import this
# module do not start a worker.
_BOOT_AT = datetime.now(tz=timezone.utc)
