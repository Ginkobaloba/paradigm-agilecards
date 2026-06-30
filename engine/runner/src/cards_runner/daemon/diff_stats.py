"""Diff statistics for the confidence gate (gate chunk 2).

`docs/design/confidence_driven_merge_gate.md` section 12.1: the gate
reads the diff locally (`git diff base...branch --numstat`) rather than
round-tripping GitHub, because the gate decides BEFORE the PR opens.

This module is pure-data plus a thin git shell. `DiffStats.from_numstat`
parses `git diff --numstat` output (testable without git); `from_worktree`
runs git and delegates to it. The glob matchers back the gate's
`sensitive_path_touched`, `schema_migration_in_diff`, and
`new_external_dependency` hard escalators.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to a compiled regex.

    `**` matches any run of characters including `/`; `*` matches any
    run except `/`; `?` matches a single non-`/` char. Everything else
    is literal. Matching is against POSIX-style forward-slash paths."""
    out: list[str] = ["^"]
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # `**/` matches zero or more leading path segments, so
                # "a/**/b" also matches "a/b"; a bare `**` matches any run.
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def matches_any_glob(path: str, patterns: Iterable[str]) -> bool:
    """True when `path` (forward-slash form) matches any glob."""
    normalized = path.replace("\\", "/")
    for pattern in patterns:
        if _glob_to_regex(pattern).match(normalized):
            return True
    return False


@dataclass(frozen=True)
class DiffStats:
    """Parsed `git diff --numstat` for a card's branch vs its base."""

    files: tuple[str, ...] = ()
    lines_added: int = 0
    lines_removed: int = 0

    @property
    def total_lines(self) -> int:
        return self.lines_added + self.lines_removed

    def any_path_matches(self, patterns: Iterable[str]) -> bool:
        pats = tuple(patterns)
        return any(matches_any_glob(f, pats) for f in self.files)

    @classmethod
    def from_numstat(cls, text: str) -> "DiffStats":
        """Parse `git diff --numstat` output.

        Each line is `<added>\\t<removed>\\t<path>`. Binary files report
        `-` for the counts (treated as 0 added/removed but the path is
        still recorded so glob matchers see it)."""
        files: list[str] = []
        added = 0
        removed = 0
        for line in text.splitlines():
            line = line.strip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            a, r, path = parts[0], parts[1], parts[2]
            files.append(path.replace("\\", "/"))
            if a.isdigit():
                added += int(a)
            if r.isdigit():
                removed += int(r)
        return cls(files=tuple(files), lines_added=added, lines_removed=removed)

    @classmethod
    def from_worktree(
        cls,
        worktree: Path,
        *,
        branch: str,
        base_branch: str,
        git_path: str = "git",
    ) -> "DiffStats":
        """Run `git diff base...branch --numstat` in the worktree.

        Returns an empty DiffStats on any git failure -- the gate treats
        an unreadable diff as "no diff signal" and leans on the other
        inputs (the caller logs the failure)."""
        try:
            result = subprocess.run(
                # --no-renames so a rename can't dodge a sensitive-path
                # escalator (a renamed file shows as add+delete of real
                # paths rather than an `old => new` entry).
                [git_path, "-C", str(worktree), "diff", "--numstat",
                 "--no-renames", f"{base_branch}...{branch}"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return cls()
        if result.returncode != 0:
            return cls()
        return cls.from_numstat(result.stdout)


__all__ = ["DiffStats", "matches_any_glob"]
