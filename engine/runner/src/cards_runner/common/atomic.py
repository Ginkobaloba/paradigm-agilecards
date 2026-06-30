"""Atomic filesystem primitives.

`atomic_write_text` (and `atomic_touch`, which builds on it) writes
through a same-directory tempfile plus `os.replace`, so a reader
never sees a half-written file. The worker uses these for its
heartbeat file and its projected card file.

`atomic_move` is the same-volume rename primitive. After the chunk 2b
cutover the card claim is a transactional store `UPDATE`, not a file
move, so the runner no longer arbitrates claims with this; it remains
a general helper (Python's `os.replace` is `MoveFileEx` on NTFS).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_move(src: Path, dst: Path) -> None:
    """Atomically move `src` to `dst` within the same volume.

    `dst` is replaced if it exists. Raises whatever `os.replace`
    raises (`FileNotFoundError`, `PermissionError`, etc.) so callers
    can handle race losses without us inventing a new exception
    hierarchy.

    The caller is responsible for ensuring `dst.parent` exists. The
    runner always works inside the canonical subfolder tree which is
    created at daemon boot.
    """
    os.replace(src, dst)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text atomically.

    Writes to a tempfile in the same directory, fsyncs, then renames.
    This is what the heartbeat file uses; it is also safe for the card
    frontmatter rewrite path.

    Tempfile lives in the same directory so the final rename is a
    cross-name move on the same volume (atomic).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # `delete=False` so the tempfile survives the `with`. We rename it
    # ourselves and let the `os.replace` win the race.
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup. We do not try to handle errors from
        # this cleanup; the original exception is what the caller
        # wants to see.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_touch(path: Path) -> None:
    """Update mtime atomically. Used for the heartbeat file."""
    atomic_write_text(path, "")
