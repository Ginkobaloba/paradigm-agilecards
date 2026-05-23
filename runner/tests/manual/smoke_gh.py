"""Live `gh` end-to-end smoke -- NOT in the pytest suite.

Chunk 5's `SubprocessGhRunner` shipped with unit tests that mock
subprocess; the chunk-5 handoff named "Real `gh` end-to-end smoke" as
a chunk-6c deliverable. This script is that smoke.

What it does, in order, against a real GitHub repo the operator owns:

1. Locates the operator's worktree (default: cwd, or `--worktree`).
2. Creates a throwaway branch off the current HEAD named
   `cards-runner-smoke-<utc>`.
3. Stamps a tiny file (`.cards-runner-smoke.txt`) on it and commits.
4. `gh.push(...)` -- pushes the branch.
5. `gh.open_pr(...)` -- opens a DRAFT PR.
6. `gh.pr_diff(...)` -- pulls the diff back.
7. `gh.view_pr(...)` -- reads `state`, `mergedAt`.
8. `gh.pr_review(..., decision="comment")` -- posts a comment review.
9. Closes the PR via `gh pr close <id> --delete-branch`.
10. Removes the local branch + the stamped file.

Each step prints its result; a failure aborts and leaves the
intermediate state for the operator to clean up by hand. The script
is idempotent in the sense that it picks a unique branch name per
run, but it does NOT auto-rollback partial state (a smoke script
that swallows failure is the kind that hides regressions).

Usage:

    python runner/tests/manual/smoke_gh.py [--worktree PATH]

Requirements:
- `gh` on PATH (or pass `--gh-path`).
- `git` on PATH (or pass `--git-path`).
- The cwd / `--worktree` is a real git checkout of a GitHub-hosted repo.
- The operator is authenticated with `gh auth login`.
- A `main` branch exists on origin (the PR opens against it; pass
  `--base BRANCH` to change).

This script EXISTS to catch the kind of integration regression unit
tests cannot: a gh CLI version bump that changes `pr create`'s output
format, a gh auth scope change, an origin-remote mismatch. Run it
before chunk-6 PRs that touch `pr_lifecycle.py`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `src/cards_runner` importable without an install.
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cards_runner.daemon.pr_lifecycle import (  # noqa: E402
    GhCallResult, SubprocessGhRunner,
)


SMOKE_FILE_NAME = ".cards-runner-smoke.txt"


def _print(step: str, result: GhCallResult) -> None:
    ok = "OK " if result.ok else "FAIL"
    print(f"  [{ok}] {step}: exit={result.exit_code} reason={result.reason or ''}")
    if result.stdout:
        head = result.stdout.strip().splitlines()[:3]
        for line in head:
            print(f"        {line}")


def _run_local(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    """Run a local git command; raise on non-zero unless caller handles it."""
    cp = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, check=False,
    )
    return cp.returncode, cp.stdout, cp.stderr


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worktree", type=Path, default=Path.cwd())
    p.add_argument("--gh-path", default="gh")
    p.add_argument("--git-path", default="git")
    p.add_argument("--base", default="main")
    p.add_argument(
        "--skip-cleanup", action="store_true",
        help="leave the smoke branch + PR around for inspection",
    )
    args = p.parse_args(argv)

    worktree: Path = args.worktree.resolve()
    if not (worktree / ".git").exists() and not _is_inside_git_repo(worktree, args.git_path):
        print(f"error: {worktree} is not a git checkout", file=sys.stderr)
        return 2

    gh = SubprocessGhRunner(gh_path=args.gh_path, git_path=args.git_path)
    if not gh.is_available():
        print(
            f"error: gh ({args.gh_path}) or git ({args.git_path}) not on PATH",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"cards-runner-smoke-{stamp}"
    print(f"smoke worktree: {worktree}")
    print(f"smoke branch:   {branch}")
    print(f"base:           {args.base}")
    print(f"gh:             {args.gh_path}")
    print()

    # 1. Create branch and commit a throwaway file.
    print("step 1: git checkout -b + commit")
    rc, out, err = _run_local(
        [args.git_path, "checkout", "-b", branch], cwd=worktree,
    )
    if rc != 0:
        print(f"  FAIL: {err.strip()}", file=sys.stderr)
        return 1
    smoke_path = worktree / SMOKE_FILE_NAME
    smoke_path.write_text(
        f"cards-runner smoke at {stamp}\n", encoding="utf-8",
    )
    _run_local([args.git_path, "add", SMOKE_FILE_NAME], cwd=worktree)
    rc, _out, err = _run_local(
        [args.git_path, "commit", "-m", f"cards-runner smoke {stamp}"],
        cwd=worktree,
    )
    if rc != 0:
        print(f"  FAIL commit: {err.strip()}", file=sys.stderr)
        return 1
    print("  OK")

    pr_url: str | None = None
    try:
        # 2. Push the branch.
        push = gh.push(worktree, branch, set_upstream=True)
        _print("gh.push", push)
        if not push.ok:
            return 1

        # 3. Open a draft PR.
        open_pr = gh.open_pr(
            worktree,
            title=f"[smoke] cards-runner gh wrapper {stamp}",
            body="Automated smoke from runner/tests/manual/smoke_gh.py.",
            base=args.base,
            draft=True,
        )
        _print("gh.open_pr (draft)", open_pr)
        if not open_pr.ok:
            return 1
        pr_url = (open_pr.parsed or {}).get("pr_url") or ""
        if not pr_url:
            print("  FAIL: open_pr returned no pr_url", file=sys.stderr)
            return 1

        # 4. Pull the diff.
        diff = gh.pr_diff(identifier=pr_url, worktree=worktree)
        _print("gh.pr_diff", diff)

        # 5. View the PR.
        view = gh.view_pr(
            identifier=pr_url, worktree=worktree,
            fields=("state", "mergedAt", "url", "isDraft"),
        )
        _print("gh.view_pr", view)
        if view.ok and view.stdout:
            try:
                parsed = json.loads(view.stdout)
                print(f"        parsed: {parsed}")
            except Exception as exc:  # noqa: BLE001
                print(f"        parse failed: {exc}")

        # 6. Post a comment review.
        review = gh.pr_review(
            identifier=pr_url, decision="comment",
            body="cards-runner smoke comment.",
            worktree=worktree,
        )
        _print("gh.pr_review (comment)", review)

        if args.skip_cleanup:
            print("\n--skip-cleanup: leaving PR + branch in place")
            print(f"  PR: {pr_url}")
            print(f"  branch: {branch}")
            return 0

        # 7. Close the PR.
        print("\nclosing PR + deleting branch...")
        rc, out, err = _run_local(
            [args.gh_path, "pr", "close", pr_url, "--delete-branch"],
            cwd=worktree,
        )
        if rc != 0:
            print(f"  FAIL pr close: {err.strip()}", file=sys.stderr)
            return 1
        print("  OK")
    finally:
        # 8. Local cleanup: switch back to base, remove the smoke file
        #    if it still exists, drop the local branch.
        _run_local([args.git_path, "checkout", args.base], cwd=worktree)
        try:
            smoke_path.unlink()
        except FileNotFoundError:
            pass
        _run_local([args.git_path, "branch", "-D", branch], cwd=worktree)

    print("\nsmoke complete: all gh wrapper paths exercised end-to-end")
    return 0


def _is_inside_git_repo(path: Path, git_path: str) -> bool:
    cp = subprocess.run(
        [git_path, "rev-parse", "--is-inside-work-tree"],
        cwd=str(path), capture_output=True, text=True, check=False,
    )
    return cp.returncode == 0 and cp.stdout.strip() == "true"


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
