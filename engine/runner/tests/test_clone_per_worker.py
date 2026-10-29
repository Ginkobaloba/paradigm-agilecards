"""R1/R2: a throwaway clone per worker, and the change handed back as data.

The daemon never runs git in a directory model-run code wrote to. Each
test plants something real and first shows it firing (positive control),
then shows the daemon's path does not reach it.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common import git_safety
from cards_runner.common.types import (
    EXIT_CLEAN,
    PATCH_DONE_NAME,
    PATCH_NAME,
    WORKER_RESULT_NAME,
    ClaimedCard,
    DaemonConfig,
    RuntimePaths,
)
from cards_runner.daemon import clones
from cards_runner.daemon.clones import PatchRejected, integrate_patch, path_allowed
from cards_runner.daemon.daemon import Daemon
from cards_runner.daemon.pr_lifecycle import GhCallResult, SubprocessGhRunner
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record, project_card_file
from cards_runner.store.sqlite_store import SqliteRepository
from cards_runner.worker_stub.patch_emit import emit_patch

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="needs git")


def _g(cwd: Path, *args: str) -> str:
    assert GIT is not None
    return subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def _project(root: Path, name: str = "proj") -> Path:
    project = root / name
    project.mkdir(parents=True)
    _g(project, "init", "-q", "-b", "main")
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    _g(project, "add", "README.md")
    _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t",
       "commit", "-q", "-m", "init")
    _g(project, "remote", "add", "origin", "https://example.invalid/o/r.git")
    return project


def _card_text(card_id: str, project: Path, *, ac_path: str = "anything") -> str:
    return textwrap.dedent(f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Clone test card
        project: {project.as_posix()}
        status: backlog
        points: 2
        stakes: low
        difficulty: shallow
        thinking_depth: shallow
        model: claude-haiku-4-5-20251001
        extended_thinking: false
        model_floor: haiku
        pin_required: false
        requires_pre_approval: false
        cost_cap_usd: null
        estimated_tokens: 0
        actual_tokens: null
        estimated_duration_minutes: 0
        actual_duration_minutes: null
        trace_id: {uuid.uuid4()}
        depends_on: []
        touches: []
        batch: bTST
        story_hash: deadbeef
        created: 2026-10-29
        started_at: null
        finished_at: null
        claimed_by: null
        model_used: null
        last_heartbeat: null
        branch: card/{card_id}
        base_branch: main
        merge_status: pending
        verified_at: null
        verified_by: null
        verifier_skipped_reason: null
        cascade_history: []
        verifier_cascade_history: []
        standup_reason: null
        ---

        ## Acceptance criteria

        ```yaml
        acceptance_criteria:
          - description: the change is present
            type: file_exists
            path: {ac_path}
        ```
        """)


class RecordingGh:
    """Records each call, and what the repo it ran in looked like then."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def is_available(self) -> bool:
        return True

    def push(self, worktree: Path, branch: str, *, set_upstream: bool = True,
             sha: str | None = None) -> GhCallResult:
        seen: dict[str, Any] = {"worktree": worktree, "branch": branch, "sha": sha}
        if sha is not None:
            seen["tree"] = _g(worktree, "ls-tree", "-r", "--name-only", sha).split()
            seen["author"] = _g(worktree, "log", "-1", "--format=%an <%ae>", sha).strip()
            seen["subject"] = _g(worktree, "log", "-1", "--format=%s", sha).strip()
            seen["parents"] = _g(worktree, "log", "-1", "--format=%P", sha).split()
        self.calls.append(("push", seen))
        return GhCallResult(ok=True)

    def open_pr(self, worktree: Path, *, title: str, body: str, base: str,
                draft: bool = False, head: str | None = None) -> GhCallResult:
        self.calls.append(("open_pr", {"worktree": worktree, "base": base, "head": head}))
        return GhCallResult(ok=True, parsed={"pr_url": "https://example.invalid/pull/1"})

    def merge_pr(self, worktree: Path, *, identifier: str, strategy: str = "squash") -> GhCallResult:
        self.calls.append(("merge_pr", {"worktree": worktree}))
        return GhCallResult(ok=True)

    def view_pr(self, **_: Any) -> GhCallResult:  # pragma: no cover - unused
        return GhCallResult(ok=True)

    def review_pr(self, **_: Any) -> GhCallResult:  # pragma: no cover - unused
        return GhCallResult(ok=True)

    def comment_pr(self, **_: Any) -> GhCallResult:  # pragma: no cover - unused
        return GhCallResult(ok=True)


def _cfg(todo_root: Path, store_spec: str) -> DaemonConfig:
    return DaemonConfig(todo_root=todo_root, store_spec=store_spec, skip_worktree=False,
                        verifier_enabled=True, verifier_cascade_disabled=True,
                        pr_gate_enabled=True)


def _claim(repo: SqliteRepository, paths: RuntimePaths, card_id: str) -> ClaimedCard:
    attempt = "att" + uuid.uuid4().hex
    assert repo.claim_card(card_id, claimed_by="tester", attempt_trace_id=attempt) is not None
    run_dir = paths.runs / attempt
    run_dir.mkdir(parents=True)
    return ClaimedCard(card_id=card_id, attempt_trace_id=attempt, trace_id=attempt,
                       run_dir=run_dir, worktree_path=run_dir / "worktree",
                       card_file=run_dir / "card.md")


def _spawned(daemon: Daemon, repo: SqliteRepository, paths: RuntimePaths,
             card_id: str, monkeypatch: pytest.MonkeyPatch) -> ClaimedCard:
    """Prep a real worker clone; the 'worker' is the test itself."""
    monkeypatch.setattr("cards_runner.daemon.daemon.spawn_worker", lambda **kw: object())
    claim = _claim(repo, paths, card_id)
    assert daemon._prepare_and_spawn(claim) is True
    claim = daemon._workers[claim.attempt_trace_id].claim
    record = repo.get_card(card_id)
    assert record is not None
    project_card_file(record, claim.card_file, verbatim=False)
    (claim.run_dir / WORKER_RESULT_NAME).write_text('{"exit_code": 0}', encoding="utf-8")
    return claim


def _exit(daemon: Daemon, claim: ClaimedCard) -> None:
    daemon._post_worker_exit(daemon._workers.pop(claim.attempt_trace_id), EXIT_CLEAN)


@pytest.fixture
def setup(repo: SqliteRepository, paths: RuntimePaths, todo_root: Path, store_spec: str,
          tmp_path: Path):  # noqa: ANN201
    project = _project(tmp_path)
    gh = RecordingGh()
    daemon = Daemon(_cfg(todo_root, store_spec), repo=repo, gh=gh)
    return project, gh, daemon


def _insert(repo: SqliteRepository, card_id: str, project: Path, **kw: Any) -> None:
    repo.create_card(card_text_to_record(_card_text(card_id, project, **kw),
                                         card_id_fallback=card_id))


# ---- the happy path, end to end --------------------------------------


def test_worker_clone_is_its_own_repo_pinned_at_the_base_sha(
    setup, repo: SqliteRepository, paths: RuntimePaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _, daemon = setup
    _insert(repo, "bTST-CL-own", project)
    claim = _spawned(daemon, repo, paths, "bTST-CL-own", monkeypatch)
    base = _g(project, "rev-parse", "main").strip()
    assert claim.base_sha == base
    # Its own .git directory: a worktree's is a FILE pointing into the project.
    assert (claim.worktree_path / ".git").is_dir()
    assert _g(claim.worktree_path, "rev-parse", "HEAD").strip() == base
    assert _g(claim.worktree_path, "remote").strip() == ""  # nowhere to push
    # No hardlinks back to the project's object files.
    for obj in (claim.worktree_path / ".git" / "objects").rglob("*"):
        if obj.is_file():
            assert obj.stat().st_nlink == 1, obj
    # And the project grew no worktree registration.
    assert not (project / ".git" / "worktrees").exists()


def test_a_clean_exit_pushes_exactly_the_one_daemon_commit(
    setup, repo: SqliteRepository, paths: RuntimePaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, gh, daemon = setup
    _insert(repo, "bTST-CL-happy", project)
    claim = _spawned(daemon, repo, paths, "bTST-CL-happy", monkeypatch)
    wt = claim.worktree_path
    (wt / "anything").write_text("present\n", encoding="utf-8")
    # The executor's own commit, with its own identity and message: neither
    # may reach what is pushed.
    _g(wt, "add", "anything")
    _g(wt, "-c", "user.email=model@example.test", "-c", "user.name=model",
       "commit", "-q", "-m", "model says: merge me")
    (wt / "uncommitted.txt").write_text("also part of the change\n", encoding="utf-8")
    (wt / ".cards-heartbeat").write_text("tick\n", encoding="utf-8")  # the worker's own
    assert emit_patch(worktree=wt, base_sha=claim.base_sha, run_dir=claim.run_dir)

    _exit(daemon, claim)
    card = repo.get_card("bTST-CL-happy")
    assert card is not None and card.status == CardStatus.DONE.value, card
    assert [c[0] for c in gh.calls] == ["push", "open_pr", "merge_pr"]
    push = gh.calls[0][1]
    integration = clones.attempt_clone_dir(paths, claim.attempt_trace_id) / "integration"
    assert push["worktree"] == integration  # never the worker's clone
    assert push["parents"] == [claim.base_sha]  # one commit, on the pinned base
    assert sorted(push["tree"]) == ["README.md", "anything", "uncommitted.txt"]
    assert ".cards-heartbeat" not in push["tree"]
    assert "model" not in push["author"]
    assert push["subject"].startswith("bTST-CL-happy:")
    assert gh.calls[1][1]["head"] == push["branch"]
    # The daemon clones are gone once the gate is done.
    assert not clones.attempt_clone_dir(paths, claim.attempt_trace_id).exists()


def test_the_verifier_checks_the_integrated_commit_not_the_worker_clone(
    setup, repo: SqliteRepository, paths: RuntimePaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, gh, daemon = setup
    _insert(repo, "bTST-CL-ver", project)
    claim = _spawned(daemon, repo, paths, "bTST-CL-ver", monkeypatch)
    # The AC's file exists in the worker clone but is excluded from the
    # patch (gitignored), so it is not part of what would ship.
    (claim.worktree_path / ".gitignore").write_text("anything\n", encoding="utf-8")
    (claim.worktree_path / "anything").write_text("only here\n", encoding="utf-8")
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha,
                      run_dir=claim.run_dir)
    seen: list[Path] = []
    real = __import__("cards_runner.daemon.daemon", fromlist=["verify_card"]).verify_card

    def spy(**kw: Any) -> Any:
        seen.append(kw["worktree"])
        head = _g(kw["worktree"], "rev-parse", "HEAD").strip()
        assert head != claim.base_sha  # the integrated commit
        return real(**kw)

    monkeypatch.setattr("cards_runner.daemon.daemon.verify_card", spy)
    _exit(daemon, claim)
    assert seen and seen[0].name == "verify" and seen[0] != claim.worktree_path
    card = repo.get_card("bTST-CL-ver")
    assert card is not None and card.status != CardStatus.DONE.value  # AC failed
    assert gh.calls == []


# ---- planted git config never runs -----------------------------------


def _plant_fsmonitor(repo_dir: Path, marker: Path) -> None:
    script = repo_dir.parent / f"fsmon-{uuid.uuid4().hex[:6]}.sh"
    script.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\n", newline="\n")
    _g(repo_dir, "config", "core.fsmonitor", script.as_posix())
    hooks = repo_dir / ".git" / "hooks"
    for name in ("pre-commit", "post-checkout", "pre-push", "post-commit"):
        (hooks / name).write_text(f"#!/bin/sh\necho {name} >> '{marker.as_posix()}'\n",
                                  newline="\n")


def test_a_plant_in_the_worker_clone_never_runs_in_the_daemon(
    setup, repo: SqliteRepository, paths: RuntimePaths, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project, gh, daemon = setup
    _insert(repo, "bTST-CL-plant", project)
    claim = _spawned(daemon, repo, paths, "bTST-CL-plant", monkeypatch)
    marker = tmp_path / "fired.txt"
    _plant_fsmonitor(claim.worktree_path, marker)
    # Positive control: an ordinary git command in that clone runs it.
    _g(claim.worktree_path, "status")
    assert marker.is_file(), "control: the plant should fire for plain git"
    marker.unlink()

    (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha,
                      run_dir=claim.run_dir)  # model-side git, hardened too
    assert not marker.exists(), "the worker's own patch git ran the plant"
    _exit(daemon, claim)
    assert not marker.exists(), "the daemon ran something planted in the worker clone"
    card = repo.get_card("bTST-CL-plant")
    # The worker clone's config is simply never read: the card proceeds.
    assert card is not None and card.status == CardStatus.DONE.value


@pytest.mark.parametrize("planted", [True, False])
def test_a_plant_in_the_project_blocks_integration_and_later_cards(
    planted: bool, setup, repo: SqliteRepository, paths: RuntimePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same-user code can still write the project's .git by absolute path.
    # The daemon's next git call there (the integration clone) refuses it.
    project, gh, daemon = setup
    _insert(repo, f"bTST-CL-proj-{planted}", project)
    _insert(repo, f"bTST-CL-projB-{planted}", project)
    claim = _spawned(daemon, repo, paths, f"bTST-CL-proj-{planted}", monkeypatch)
    (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha,
                      run_dir=claim.run_dir)
    if planted:
        (project / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\nexit 0\n",
                                                                  newline="\n")
    _exit(daemon, claim)
    card = repo.get_card(f"bTST-CL-proj-{planted}")
    assert card is not None
    if not planted:  # control
        assert card.status == CardStatus.DONE.value
        return
    assert card.status == CardStatus.BLOCKED.value and gh.calls == []
    blocked = [e for e in repo.list_events(card.card_id) if e.type == "blocked"]
    assert blocked[-1].payload.get("error") == "repo_untrusted"
    later = _claim(repo, paths, f"bTST-CL-projB-{planted}")
    assert daemon._prepare_and_spawn(later) is False
    assert not later.worktree_path.exists()


def test_a_plant_in_the_integration_clone_during_verification_blocks_the_push(
    setup, repo: SqliteRepository, paths: RuntimePaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The verifier's shell ACs are model-written and run while the
    # integration clone exists. A config write there is caught before push.
    project, gh, daemon = setup
    _insert(repo, "bTST-CL-int", project)
    claim = _spawned(daemon, repo, paths, "bTST-CL-int", monkeypatch)
    (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha,
                      run_dir=claim.run_dir)
    real = __import__("cards_runner.daemon.daemon", fromlist=["verify_card"]).verify_card

    def tampering_verifier(**kw: Any) -> Any:
        integration = Path(kw["worktree"]).parent / "integration"
        with (integration / ".git" / "config").open("a", encoding="utf-8") as fh:
            fh.write("[core]\n\tsshCommand = evil\n")
        return real(**kw)

    monkeypatch.setattr("cards_runner.daemon.daemon.verify_card", tampering_verifier)
    _exit(daemon, claim)
    card = repo.get_card("bTST-CL-int")
    assert card is not None and card.status == CardStatus.BLOCKED.value
    assert [c[0] for c in gh.calls] == []  # refused before git push ran


# ---- the patch is data, checked before anything is committed ---------


def test_a_missing_or_cut_off_patch_blocks_the_card(
    setup, repo: SqliteRepository, paths: RuntimePaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, gh, daemon = setup
    for case in ("missing", "cut"):
        cid = f"bTST-CL-{case}"
        _insert(repo, cid, project)
        claim = _spawned(daemon, repo, paths, cid, monkeypatch)
        (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")
        if case == "cut":
            assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha,
                              run_dir=claim.run_dir)
            data = (claim.run_dir / PATCH_NAME).read_bytes()
            (claim.run_dir / PATCH_NAME).write_bytes(data[: len(data) // 2])
        _exit(daemon, claim)
        card = repo.get_card(cid)
        assert card is not None and card.status == CardStatus.BLOCKED.value, case
        blocked = [e for e in repo.list_events(cid) if e.type == "blocked"]
        assert blocked[-1].payload.get("error") == "patch_refused"
    assert gh.calls == []


def _patch_for(tmp: Path, build) -> bytes:  # noqa: ANN001
    """A real `git diff --binary` patch from a scratch repo `build` edits."""
    scratch = _project(tmp, "scratch-" + uuid.uuid4().hex[:6])
    base = _g(scratch, "rev-parse", "HEAD").strip()
    build(scratch)
    _g(scratch, "add", "-A")
    return subprocess.run([GIT, "diff", "--cached", "--binary", "--full-index", base],
                          cwd=scratch, check=True, capture_output=True).stdout


def _integrate(tmp: Path, project: Path, patch: bytes) -> str:
    patch_file = tmp / f"p-{uuid.uuid4().hex[:6]}.patch"
    patch_file.write_bytes(patch)
    base = _g(project, "rev-parse", "main").strip()
    return integrate_patch(project_dir=project, base_branch="main", base_sha=base,
                           branch="card/x/1", patch_file=patch_file,
                           dest=tmp / f"int-{uuid.uuid4().hex[:6]}", message="m")


def test_a_plain_patch_integrates(tmp_path: Path) -> None:
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    patch = _patch_for(tmp_path, lambda r: (r / "src.py").write_text("x = 1\n"))
    sha = _integrate(tmp_path, project, patch)  # control for the refusals below
    assert len(sha) == 40


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks")
def test_a_patch_adding_a_symlink_or_submodule_is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    blob = subprocess.run([GIT, "hash-object", "--stdin"], input=b"../../outside",
                          capture_output=True, check=True).stdout.decode().strip()
    symlink = (f"diff --git a/link b/link\nnew file mode 120000\nindex {'0' * 40}..{blob}\n"
               f"--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+../../outside\n"
               f"\\ No newline at end of file\n").encode()
    with pytest.raises(PatchRejected, match="symlink or submodule"):
        _integrate(tmp_path, project, symlink)
    sub_sha = _g(project, "rev-parse", "HEAD").strip()
    gitlink = (f"diff --git a/vendor b/vendor\nnew file mode 160000\nindex {'0' * 40}..{sub_sha}\n"
               f"--- /dev/null\n+++ b/vendor\n@@ -0,0 +1 @@\n+Subproject commit {sub_sha}\n"
               ).encode()
    with pytest.raises(PatchRejected):
        _integrate(tmp_path, project, gitlink)


@pytest.mark.parametrize("path", [
    ".GIT/config", "sub/.git/hooks/pre-push", "GIT~1/config", "a/.git./x", "a/.git /x",
])
def test_a_patch_aliasing_dot_git_is_refused(path: str, tmp_path: Path) -> None:
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    blob = subprocess.run([GIT, "hash-object", "--stdin"], input=b"evil\n",
                          capture_output=True, check=True).stdout.decode().strip()
    patch = (f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex {'0' * 40}..{blob}\n"
             f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+evil\n").encode()
    # Refused by git's protectNTFS/HFS at apply or by the daemon's own
    # check after it: either way nothing is committed.
    with pytest.raises(PatchRejected):
        _integrate(tmp_path, project, patch)


@pytest.mark.parametrize("path,ok", [
    ("src/app.py", True), ("docs/a b.md", True), (".github/workflows/ci.yml", True),
    (".gitignore", True), ("a/.gitattributes", True),
    ("/etc/passwd", False), ("../x", False), ("a/../b", False), ("a\\b", False),
    ("C:x", False), ("file.txt:stream", False), (".git", False), (".GIT/config", False),
    ("x/.Git/hooks", False), (".git./x", False), (".git  /x", False), ("GIT~1/x", False),
    ("a//b", False), ("./a", False), ("", False),
])
def test_path_allowed(path: str, ok: bool) -> None:
    assert path_allowed(path) is ok


def test_an_oversized_patch_is_refused(tmp_path: Path) -> None:
    (tmp_path / PATCH_NAME).write_bytes(b"x" * 101)
    (tmp_path / PATCH_DONE_NAME).write_text("sha256=whatever\n", encoding="utf-8")
    with pytest.raises(PatchRejected, match="exceeds"):
        clones.read_patch(tmp_path, max_bytes=100)


# ---- the push names the sha, not a branch ------------------------------


def test_push_with_a_sha_pushes_that_commit_to_the_branch(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    seen: list[list[str]] = []
    runner = SubprocessGhRunner()
    monkeypatch.setattr(runner, "_run", lambda args, cwd: (seen.append(args), GhCallResult(ok=True))[1])
    runner.push(tmp_path, "card/x/1", sha="a" * 40)
    runner.push(tmp_path, "card/x/1")  # skip-worktree mode keeps the old form
    assert seen[0][1:] == ["push", "origin", f"{'a' * 40}:refs/heads/card/x/1"]
    assert seen[1][1:] == ["push", "-u", "origin", "card/x/1"]


def test_the_merge_gate_fails_closed_without_an_integrated_commit(
    setup, repo: SqliteRepository, paths: RuntimePaths,
) -> None:
    project, gh, daemon = setup
    _insert(repo, "bTST-CL-noint", project)
    record = repo.get_card("bTST-CL-noint")
    assert record is not None
    claim = _claim(repo, paths, "bTST-CL-noint")
    outcome = daemon._merge_gate.apply(claim, record, verified_at="2026-10-29T00:00:00Z")
    assert outcome.to_status == CardStatus.BLOCKED.value
    assert outcome.extra_payload == {"error": "not_integrated"}
    assert gh.calls == []
    # And with one (a trusted repo standing in for the clone), it pushes it.
    git_safety.assert_trusted(project, first_sight_ok=True)
    done = replace(claim, integration_path=project, integrated_sha="b" * 40)
    daemon._merge_gate.gh = _NoInspectGh(gh)
    daemon._merge_gate.apply(done, record, verified_at="2026-10-29T00:00:00Z")
    assert gh.calls[0] == ("push", {"worktree": project, "branch": gh.calls[0][1]["branch"],
                                    "sha": "b" * 40})


class _NoInspectGh(RecordingGh):
    def __init__(self, inner: RecordingGh) -> None:
        self.calls = inner.calls

    def push(self, worktree: Path, branch: str, *, set_upstream: bool = True,
             sha: str | None = None) -> GhCallResult:
        self.calls.append(("push", {"worktree": worktree, "branch": branch, "sha": sha}))
        return GhCallResult(ok=True)


# ---- W1b: a case-mismatched branch remote is hashed --------------------


def test_branch_remote_names_match_case_sensitively_like_git(tmp_path: Path) -> None:
    project = _project(tmp_path)
    # git's own behaviour: remote subsection names are case-sensitive, so
    # `ORIGIN` is not the configured `origin` (git would treat it as a URL
    # or path, which is why it must not be exempt from the hash).
    probe = subprocess.run([GIT, "config", "--get", "remote.ORIGIN.url"], cwd=project,
                           capture_output=True)
    assert probe.returncode == 1
    assert _g(project, "config", "--get", "remote.origin.url").strip()

    common = (project / ".git").resolve()
    before = git_safety.fingerprint(common)
    _g(project, "config", "branch.main.remote", "origin")
    assert git_safety.fingerprint(common) == before  # exempt: a real remote
    _g(project, "config", "branch.main.remote", "ORIGIN")
    assert git_safety.fingerprint(common) != before  # hashed: not a remote
