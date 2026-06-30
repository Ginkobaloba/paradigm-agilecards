"""Thin `gh` CLI wrapper for the chunk 4 merge gate.

RUNNER_CONTRACT.md "Merge gates" routes a verifier-pass card through a
tier-aware gate; chunk 4 implements the GitHub side of that gate by
shelling out to the GitHub CLI. This module is intentionally narrow: it
exposes the four operations the merge gate calls (`is_available`,
`push`, `open_pr`, `merge_pr`) and nothing else. Every method takes a
`worktree: Path` and runs `gh` inside it so the right git repo is
implicit.

The runner deliberately does NOT depend on the `gh` Python wrapper: gh
is a stable CLI surface, and subprocess + JSON parsing keeps the
dependency footprint smaller. Each call returns a structured
`GhCallResult`; the caller decides whether a non-zero exit is fatal.

`NullGhRunner` is the chunk-3 behavioral fallback for callers that do
not (or cannot) talk to GitHub. It refuses every call by returning a
`GhCallResult(ok=False, reason="gh disabled")`. The merge gate uses
this when `DaemonConfig.pr_gate_enabled` is False so chunk-3 callers
keep their "verifier pass -> done" semantics.

Tests inject a `FakeGhRunner` (in `tests/test_pr_lifecycle.py`) that
records calls and returns scripted results. Nothing in the runner
proper imports the real subprocess module in test scope.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


log = logging.getLogger(__name__)


# Conservative wall-clock for any single gh subprocess. A merge race
# can keep `gh pr merge` blocking for a while; we let it run for two
# minutes before treating it as a hung process.
_DEFAULT_GH_TIMEOUT_SEC: float = 120.0


@dataclass(frozen=True)
class GhCallResult:
    """One subprocess call's result, in structured form.

    `ok` is the only field every call site reads. `reason` carries a
    one-line explanation on failure (gh exit code, network error, etc.)
    that becomes part of the lifecycle event payload so a human can
    diagnose a stuck card without re-running gh by hand.
    """

    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    reason: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)


class GhRunner(Protocol):
    """Protocol for the gh wrappers the merge gate consumes."""

    def is_available(self) -> bool: ...

    def push(self, worktree: Path, branch: str, *, set_upstream: bool = True) -> GhCallResult: ...

    def open_pr(
        self,
        worktree: Path,
        *,
        title: str,
        body: str,
        base: str,
        draft: bool = False,
    ) -> GhCallResult: ...

    def merge_pr(
        self,
        worktree: Path,
        *,
        identifier: str,
        strategy: str = "squash",
    ) -> GhCallResult: ...

    def view_pr(
        self,
        *,
        identifier: str,
        fields: tuple[str, ...] = ("state", "mergedAt", "url"),
        worktree: Path | None = None,
    ) -> GhCallResult: ...

    def pr_diff(
        self,
        *,
        identifier: str,
        worktree: Path | None = None,
    ) -> GhCallResult: ...

    def pr_review(
        self,
        *,
        identifier: str,
        decision: str,
        body: str,
        worktree: Path | None = None,
    ) -> GhCallResult: ...


class NullGhRunner:
    """Refuses every call with a structured `gh disabled` reason.

    The merge gate uses this when `DaemonConfig.pr_gate_enabled` is
    False, so chunk-4 callers that opted not to wire GitHub still get
    a uniform return shape (the gate decides what to do with the ok=False
    result). Also useful in tests that want to assert "the gate never
    tried to push".
    """

    def is_available(self) -> bool:
        return False

    def push(self, worktree: Path, branch: str, *, set_upstream: bool = True) -> GhCallResult:
        del worktree, branch, set_upstream
        return GhCallResult(ok=False, reason="gh disabled")

    def open_pr(
        self,
        worktree: Path,
        *,
        title: str,
        body: str,
        base: str,
        draft: bool = False,
    ) -> GhCallResult:
        del worktree, title, body, base, draft
        return GhCallResult(ok=False, reason="gh disabled")

    def merge_pr(
        self,
        worktree: Path,
        *,
        identifier: str,
        strategy: str = "squash",
    ) -> GhCallResult:
        del worktree, identifier, strategy
        return GhCallResult(ok=False, reason="gh disabled")

    def view_pr(
        self,
        *,
        identifier: str,
        fields: tuple[str, ...] = ("state", "mergedAt", "url"),
        worktree: Path | None = None,
    ) -> GhCallResult:
        del identifier, fields, worktree
        return GhCallResult(ok=False, reason="gh disabled")

    def pr_diff(
        self,
        *,
        identifier: str,
        worktree: Path | None = None,
    ) -> GhCallResult:
        del identifier, worktree
        return GhCallResult(ok=False, reason="gh disabled")

    def pr_review(
        self,
        *,
        identifier: str,
        decision: str,
        body: str,
        worktree: Path | None = None,
    ) -> GhCallResult:
        del identifier, decision, body, worktree
        return GhCallResult(ok=False, reason="gh disabled")


@dataclass
class SubprocessGhRunner:
    """Real gh + git wrapper. Subprocess-based, mockable.

    `gh_path` defaults to the binary on PATH (`gh`); a deployment that
    pins a specific install can override. `git_path` does the same for
    git, because the merge gate's `push` actually shells `git push` --
    `gh` itself does not expose a push verb, so we use the underlying
    git in the worktree's directory.
    """

    gh_path: str = "gh"
    git_path: str = "git"
    timeout_sec: float = _DEFAULT_GH_TIMEOUT_SEC
    env: dict[str, str] | None = None

    def is_available(self) -> bool:
        return shutil.which(self.gh_path) is not None and shutil.which(self.git_path) is not None

    def push(
        self, worktree: Path, branch: str, *, set_upstream: bool = True
    ) -> GhCallResult:
        # `git push -u origin branch` from inside the per-card worktree.
        # The runner's worktree was created off `base_branch`; the executor
        # committed on `card/<id>`. The push uses `origin` as the remote
        # (the runner does not yet support multi-remote projects; chunk 5+
        # can add that when a project needs it).
        args = [self.git_path, "push"]
        if set_upstream:
            args.append("-u")
        args.extend(["origin", branch])
        return self._run(args, cwd=worktree)

    def open_pr(
        self,
        worktree: Path,
        *,
        title: str,
        body: str,
        base: str,
        draft: bool = False,
    ) -> GhCallResult:
        # `gh pr create --base BASE --title T --body B [--draft]` returns
        # the PR URL on stdout. We parse it back into the result so the
        # caller can stamp the URL into the card's lifecycle event.
        args = [
            self.gh_path, "pr", "create",
            "--base", base,
            "--title", title,
            "--body", body,
        ]
        if draft:
            args.append("--draft")
        result = self._run(args, cwd=worktree)
        if result.ok:
            url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            return GhCallResult(
                ok=True,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                parsed={"pr_url": url},
            )
        return result

    def merge_pr(
        self,
        worktree: Path,
        *,
        identifier: str,
        strategy: str = "squash",
    ) -> GhCallResult:
        # `gh pr merge <id> --squash --auto --delete-branch`. `--auto`
        # tells gh to merge when the merge gate clears (CI green, no
        # conflicts); we set it for tier-1/2 auto-merge so the runner does
        # not have to poll.
        strategy_flag = {
            "squash": "--squash",
            "merge": "--merge",
            "rebase": "--rebase",
        }.get(strategy, "--squash")
        args = [
            self.gh_path, "pr", "merge", identifier,
            strategy_flag,
            "--auto",
            "--delete-branch",
        ]
        return self._run(args, cwd=worktree)

    def view_pr(
        self,
        *,
        identifier: str,
        fields: tuple[str, ...] = ("state", "mergedAt", "url"),
        worktree: Path | None = None,
    ) -> GhCallResult:
        """`gh pr view <id> --json state,mergedAt,...`.

        The unblocker (chunk 5) polls this each tick for `blocked` cards
        whose merge_status is `open` or `requires_review`. A PR URL or
        a PR number both work as the identifier; gh resolves either.
        When `worktree` is None we let gh auto-detect the repo from the
        cwd, which works because the unblocker can also pass a fully
        qualified URL and skip the repo discovery.
        """
        args = [
            self.gh_path, "pr", "view", identifier,
            "--json", ",".join(fields),
        ]
        result = self._run(args, cwd=worktree)
        if result.ok and result.stdout:
            parsed = parse_pr_view_json(result.stdout)
            return GhCallResult(
                ok=True,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                parsed=parsed,
            )
        return result

    def pr_diff(
        self,
        *,
        identifier: str,
        worktree: Path | None = None,
    ) -> GhCallResult:
        """`gh pr diff <id>`. Used by the sibling reviewer for context."""
        args = [self.gh_path, "pr", "diff", identifier]
        return self._run(args, cwd=worktree)

    def pr_review(
        self,
        *,
        identifier: str,
        decision: str,
        body: str,
        worktree: Path | None = None,
    ) -> GhCallResult:
        """`gh pr review <id> --approve|--request-changes|--comment`.

        Decisions other than `approve|request_changes|comment` fall back
        to `--comment` so a reviewer with an unrecognized verdict still
        produces a visible note on the PR rather than silently no-op.
        """
        decision_flag = {
            "approve": "--approve",
            "request_changes": "--request-changes",
            "comment": "--comment",
        }.get(decision, "--comment")
        args = [
            self.gh_path, "pr", "review", identifier,
            decision_flag,
            "--body", body,
        ]
        return self._run(args, cwd=worktree)

    def _run(self, args: list[str], *, cwd: Path | None) -> GhCallResult:
        env = os.environ.copy()
        if self.env is not None:
            env.update(self.env)
        try:
            cp = subprocess.run(
                args,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
        except FileNotFoundError as exc:
            log.error("binary not found: %s", exc)
            return GhCallResult(
                ok=False, reason=f"binary not found: {exc.filename}"
            )
        except subprocess.TimeoutExpired as exc:
            log.warning("gh call timed out after %ss: %s", self.timeout_sec, args)
            return GhCallResult(
                ok=False,
                reason=f"gh timed out after {self.timeout_sec}s",
                stdout=(exc.stdout or "").decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=(exc.stderr or "").decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            )
        ok = cp.returncode == 0
        reason = "" if ok else f"exit {cp.returncode}: {cp.stderr.strip()[:200]}"
        return GhCallResult(
            ok=ok,
            stdout=cp.stdout or "",
            stderr=cp.stderr or "",
            exit_code=cp.returncode,
            reason=reason,
        )


def parse_pr_view_json(stdout: str) -> dict[str, Any]:
    """Helper for `gh pr view --json ...`. Not used by chunk 4 directly.

    Reserved for chunk 5's poll-for-merged unblocker: once a PR opened
    by a sibling/human gate is merged externally, the daemon will need
    to read `state` and `mergedAt` to progress the card to `done`.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
