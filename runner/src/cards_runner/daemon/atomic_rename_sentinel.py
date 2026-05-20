"""Atomic-rename-test sentinel.

Per design item 12, the daemon refuses to enable parallel mode unless
the host has been verified to handle `os.replace` atomically under
concurrent contention. The check is host-scoped because NTFS plus
filesystem filters plus antivirus plus indexing plus OneDrive can
break atomicity in non-obvious ways.

There are two flavors of the test:

1. **Embedded test.** A pure-Python racy rename test we can run
   ourselves at daemon boot. Cheap, no external dependency. The
   sentinel is written when this passes.

2. **PowerShell test.** The repo ships
   `agile-cards/tests/atomic_rename_test.ps1`. The daemon prefers
   the embedded test for boot ergonomics; the PowerShell test
   remains the canonical reference for the more aggressive race
   pattern (16 jobs, 50 rounds) and is what humans run when
   debugging a flaky machine.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import shutil
import sys
import tempfile
from pathlib import Path

from ..common.atomic import atomic_write_text
from ..common.types import RuntimePaths


def _race_worker(src: str, dst: str, result_q: "multiprocessing.Queue[str]") -> None:
    """Single attempt: rename src -> dst. Reports outcome on the queue.

    Lives at module scope (not a closure) so it pickles for the
    spawn-based multiprocessing start method on Windows.
    """
    try:
        os.replace(src, dst)
        result_q.put("WIN")
    except FileNotFoundError:
        result_q.put("LOSE")
    except OSError as exc:
        # PermissionError, etc. Treat as LOSE since the source was
        # taken by another process.
        result_q.put(f"LOSE:{type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        result_q.put(f"WEIRD:{exc!r}")


log = logging.getLogger(__name__)


def is_sentinel_present(paths: RuntimePaths) -> bool:
    return paths.atomic_rename_sentinel.is_file()


def write_sentinel(paths: RuntimePaths) -> None:
    """Stamp the sentinel.

    Idempotent. Caller is expected to have verified atomicity first;
    this is a pure marker.
    """
    atomic_write_text(
        paths.atomic_rename_sentinel,
        "atomic-rename-test: PASS (embedded test)\n",
    )


def run_embedded_test(
    *,
    parallel: int = 8,
    rounds: int = 8,
    work_dir: Path | None = None,
) -> bool:
    """Race N processes renaming the same source file to distinct
    destinations. Exactly one rename per round should win.

    We use processes (not threads) because production claim races
    are between separate daemon processes. NTFS atomicity holds at
    process granularity but can be subverted by thread-level
    interleavings inside one process; threads are the wrong model.

    Returns True iff every round ended with exactly one win and zero
    weird exceptions. Boot stays fast: 8 procs x 8 rounds at ~50ms
    each. The PowerShell test (16 jobs x 50 rounds) remains the
    canonical heavyweight reference.
    """
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="cards-rename-test-"))
    ctx = multiprocessing.get_context("spawn")
    try:
        for i in range(rounds):
            round_dir = work_dir / f"round-{i}"
            round_dir.mkdir(parents=True, exist_ok=True)
            src = round_dir / "src.txt"
            src.write_text(f"card-{i}", encoding="utf-8")
            result_q: "multiprocessing.Queue[str]" = ctx.Queue()
            procs = [
                ctx.Process(
                    target=_race_worker,
                    args=(str(src), str(round_dir / f"dst-{j}.txt"), result_q),
                )
                for j in range(parallel)
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=15)
            results: list[str] = []
            while not result_q.empty():
                results.append(result_q.get_nowait())
            wins = sum(1 for r in results if r == "WIN")
            weird = sum(1 for r in results if r.startswith("WEIRD"))
            if wins != 1 or weird != 0:
                log.warning(
                    "atomic_rename embedded test failed in round %d: "
                    "wins=%d weird=%d results=%r",
                    i, wins, weird, results,
                )
                return False
        return True
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def ensure_sentinel_or_force_serial(
    paths: RuntimePaths,
    *,
    max_parallel: int,
) -> int:
    """Boot-time check.

    If the sentinel is present, returns `max_parallel` unchanged.
    Otherwise runs the embedded test and either stamps the sentinel
    (returning `max_parallel`) or forces parallel = 1.

    Per design item 12 the daemon MUST refuse parallel until the
    test passes on this machine.
    """
    if is_sentinel_present(paths):
        log.debug("atomic-rename sentinel present; parallel mode allowed")
        return max_parallel
    log.info("atomic-rename sentinel missing; running embedded test")
    passed = run_embedded_test()
    if passed:
        write_sentinel(paths)
        log.info(
            "atomic-rename embedded test PASSED; sentinel stamped at %s",
            paths.atomic_rename_sentinel,
        )
        return max_parallel
    log.warning(
        "atomic-rename embedded test FAILED on this host; "
        "forcing max_parallel=1. Re-run "
        "agile-cards/tests/atomic_rename_test.ps1 for diagnostics."
    )
    return 1
