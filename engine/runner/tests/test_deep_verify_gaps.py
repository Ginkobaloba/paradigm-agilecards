"""Gap tests from the independent deep verify of #87 (tests lens, L6).

Each passed on 7ff2aab and fails on the mutant named in its comment: 36
protections the shipped suite did not pin (37 of the lens's 42 surviving
mutants). Written by the verifier, shipped as written so the re-verify can
mutate against them; only the two debug prints were removed."""
# ruff: noqa: F811 - the `setup` fixture is imported by name, the pytest idiom
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common import git_safety
from cards_runner.common.types import DaemonConfig
from cards_runner.daemon import clones
from cards_runner.daemon.clones import CloneError, PatchRejected, integrate_patch, prepare_verify_clone
from cards_runner.daemon.daemon import Daemon, _WorkerHandle
from cards_runner.daemon.pr_lifecycle import SubprocessGhRunner, GhCallResult
from cards_runner.store import CardStatus
from cards_runner.worker_stub.patch_emit import emit_patch
from cards_runner.metrics import events as ev

from tests.test_clone_per_worker import (  # noqa: F401  (fixtures + helpers)
    GIT, _exit, _g, _insert, _patch_for, _project, _spawned, _claim, setup,
)


def _emit(claim, edit=lambda wt: (wt / "anything").write_text("x\n", encoding="utf-8")):
    edit(claim.worktree_path)
    assert emit_patch(worktree=claim.worktree_path, base_sha=claim.base_sha, run_dir=claim.run_dir)


# D03 -----------------------------------------------------------------
def test_gap_later_claim_after_project_plant_is_blocked_not_requeued(
    setup, repo, paths, monkeypatch,
):
    project, gh, daemon = setup
    _insert(repo, "bTST-GP-a", project)
    _insert(repo, "bTST-GP-b", project)
    claim = _spawned(daemon, repo, paths, "bTST-GP-a", monkeypatch)
    _emit(claim)
    (project / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\nexit 0\n", newline="\n")
    _exit(daemon, claim)
    later = _claim(repo, paths, "bTST-GP-b")
    assert daemon._prepare_and_spawn(later) is False
    b = repo.get_card("bTST-GP-b")
    assert b is not None and b.status == CardStatus.BLOCKED.value, b.status  # not a backlog livelock


# D02 -----------------------------------------------------------------
def test_gap_the_patch_applied_is_the_hash_checked_copy(setup, repo, paths, monkeypatch, tmp_path):
    project, gh, daemon = setup
    _insert(repo, "bTST-GP-toctou", project)
    claim = _spawned(daemon, repo, paths, "bTST-GP-toctou", monkeypatch)
    _emit(claim)
    evil = _patch_for(tmp_path, lambda r: (r / "evil.txt").write_text("pwned\n"))
    real = clones.read_patch
    import cards_runner.daemon.daemon as dm

    def read_then_rewrite(run_dir, **kw):
        data = real(run_dir, **kw)
        (run_dir / "result.patch").write_bytes(evil)  # sibling rewrites after the read
        return data

    monkeypatch.setattr(dm, "read_patch", read_then_rewrite)
    _exit(daemon, claim)
    pushes = [a for n, a in gh.calls if n == "push"]
    assert pushes and "evil.txt" not in pushes[0]["tree"], pushes


# D07 -----------------------------------------------------------------
def test_gap_a_refused_patch_never_reaches_the_verifier(setup, repo, paths, monkeypatch):
    project, gh, daemon = setup
    _insert(repo, "bTST-GP-nover", project)
    claim = _spawned(daemon, repo, paths, "bTST-GP-nover", monkeypatch)
    (claim.worktree_path / "anything").write_text("x\n", encoding="utf-8")  # no patch emitted
    import cards_runner.daemon.daemon as dm
    ran: list[Any] = []
    real = dm.verify_card
    monkeypatch.setattr(dm, "verify_card", lambda **kw: (ran.append(kw["worktree"]), real(**kw))[1])
    _exit(daemon, claim)
    assert ran == [], ran


# D10 -----------------------------------------------------------------
def test_gap_verify_clone_failure_never_falls_back_to_the_worker_clone(
    setup, repo, paths, monkeypatch,
):
    project, gh, daemon = setup
    _insert(repo, "bTST-GP-vcf", project)
    claim = _spawned(daemon, repo, paths, "bTST-GP-vcf", monkeypatch)
    _emit(claim)
    import cards_runner.daemon.daemon as dm
    ran: list[Any] = []
    real = dm.verify_card
    monkeypatch.setattr(dm, "verify_card", lambda **kw: (ran.append(kw["worktree"]), real(**kw))[1])

    def boom(**kw):
        raise CloneError("disk full")

    monkeypatch.setattr(dm, "prepare_verify_clone", boom)
    _exit(daemon, claim)
    assert ran == [] and gh.calls == [], (ran, gh.calls)


# PL03 ----------------------------------------------------------------
def test_gap_gh_pr_create_names_the_head_branch(monkeypatch, tmp_path):
    seen: list[list[str]] = []
    runner = SubprocessGhRunner()
    monkeypatch.setattr(runner, "_run", lambda args, cwd: (seen.append(args), GhCallResult(ok=True, stdout="https://x/y/pull/1"))[1])
    runner.open_pr(tmp_path, title="t", body="b", base="main", head="card/x/1")
    args = seen[0]
    assert "--head" in args and args[args.index("--head") + 1] == "card/x/1", args


# C02 -----------------------------------------------------------------
def test_gap_integrated_commit_must_be_one_commit_on_the_base(tmp_path, monkeypatch):
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    patch = _patch_for(tmp_path, lambda r: (r / "src.py").write_text("x = 1\n"))
    real_git = clones._git

    def sibling_adds_a_commit(cwd, *args, **kw):
        out = real_git(cwd, *args, **kw)
        if args and args[0] == "commit":
            real_git(cwd, "commit", "--quiet", "--allow-empty", "-m", "extra",
                     extra_config=tuple(git_safety.commit_identity()))
        return out

    monkeypatch.setattr(clones, "_git", sibling_adds_a_commit)
    pf = tmp_path / "p.patch"
    pf.write_bytes(patch)
    base = _g(project, "rev-parse", "main").strip()
    with pytest.raises(PatchRejected, match="not one commit"):
        integrate_patch(project_dir=project, base_branch="main", base_sha=base, branch="card/x/1",
                        patch_file=pf, dest=tmp_path / "int", message="m")


# C03 + C10 -----------------------------------------------------------
def test_gap_verify_clone_has_no_remote_and_integration_keeps_the_project_origin(tmp_path):
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    patch = _patch_for(tmp_path, lambda r: (r / "src.py").write_text("x = 1\n"))
    pf = tmp_path / "p.patch"
    pf.write_bytes(patch)
    base = _g(project, "rev-parse", "main").strip()
    idir = tmp_path / "int"
    sha = integrate_patch(project_dir=project, base_branch="main", base_sha=base, branch="card/x/1",
                          patch_file=pf, dest=idir, message="m")
    assert _g(idir, "config", "--get", "remote.origin.url").strip() == "https://example.invalid/o/r.git"
    vdir = tmp_path / "ver"
    prepare_verify_clone(integration=idir, branch="card/x/1", sha=sha, dest=vdir)
    assert _g(vdir, "remote").strip() == ""


# C05 + C06 + C11 + C12 -----------------------------------------------
def test_gap_worker_clone_is_single_branch_no_tags_pinned_and_unknown_to_the_daemon(tmp_path):
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    _g(project, "tag", "v1")
    _g(project, "checkout", "-q", "-b", "other")
    (project / "secret-branch.txt").write_text("not for the worker\n")
    _g(project, "add", "-A")
    _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "o")
    _other_sha = _g(project, "rev-parse", "other").strip()
    _g(project, "checkout", "-q", "main")
    (project / "second.txt").write_text("2\n")
    _g(project, "add", "-A")
    _g(project, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "m2")
    older = _g(project, "rev-parse", "main~1").strip()

    real_resolve = clones.resolve_base_sha
    clones.resolve_base_sha = lambda *a, **k: older  # a ref that moved after the pin
    try:
        wt = tmp_path / "wt"
        clones.prepare_worker_clone(project_dir=project, base_branch="main", branch="card/x/1", dest=wt)
    finally:
        clones.resolve_base_sha = real_resolve
    assert _g(wt, "rev-parse", "HEAD").strip() == older          # C11
    # NOTE (finding): --single-branch does NOT keep other branches' objects out of a
    # LOCAL-path clone (objects/ is copied wholesale), so only refs are asserted here.
    assert sorted(_g(wt, "for-each-ref", "--format=%(refname)").split()) == [
        "refs/heads/card/x/1", "refs/heads/main"]
    assert _g(wt, "tag").strip() == ""                            # C06
    with pytest.raises(git_safety.UntrustedRepo):                 # C12
        git_safety.run_git([GIT, "status"], cwd=wt)


# C16 -----------------------------------------------------------------
def test_gap_an_empty_patch_integrates_to_the_base(tmp_path):
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    pf = tmp_path / "e.patch"
    pf.write_bytes(b"")
    base = _g(project, "rev-parse", "main").strip()
    sha = integrate_patch(project_dir=project, base_branch="main", base_sha=base, branch="card/x/1",
                          patch_file=pf, dest=tmp_path / "int", message="m")
    assert sha == base


# C13 + D06 -----------------------------------------------------------
def test_gap_stale_clone_reaper_skips_in_flight_and_runs_each_tick(setup, repo, paths):
    project, gh, daemon = setup
    root = clones.clones_root(paths)
    for att in ("att-live", "att-stale"):
        (root / att / "integration").mkdir(parents=True)
        (root / att / "integration" / "f").write_text("x")
    assert clones.reap_stale_clones(paths, {"att-live"}) == 1
    assert (root / "att-live").exists() and not (root / "att-stale").exists()
    (root / "att-stale2" / "integration").mkdir(parents=True)
    daemon._boot()
    daemon._tick()
    assert not (root / "att-stale2").exists(), "the tick did not reap a stale clone dir"


# D01 -----------------------------------------------------------------
def test_gap_reap_ends_the_workers_job_before_post_exit(setup, repo, paths, monkeypatch):
    project, gh, daemon = setup
    order: list[str] = []

    class Proc:
        def poll(self):
            return 0

        def end_job(self):
            order.append("end_job")

        def kill_tree(self, **kw):  # pragma: no cover
            order.append("kill_tree")

    _insert(repo, "bTST-GP-reap", project)
    claim = _claim(repo, paths, "bTST-GP-reap")
    daemon._workers[claim.attempt_trace_id] = _WorkerHandle(claim=claim, process=Proc(), spawned_at=0.0)
    monkeypatch.setattr(daemon, "_post_worker_exit", lambda h, rc: order.append("post_exit"))
    daemon._reap_workers({"worker_exits": 0})
    assert order == ["end_job", "post_exit"], order


# D04 / D05 -----------------------------------------------------------
def test_gap_shadow_gate_diff_comes_from_the_integration_clone(
    repo, paths, todo_root, store_spec, tmp_path, monkeypatch,
):
    from tests.test_clone_per_worker import RecordingGh, _cfg
    project = _project(tmp_path)
    gh = RecordingGh()
    cfg = _cfg(todo_root, store_spec)
    from dataclasses import replace
    cfg = replace(cfg, ledger_enabled=True)
    daemon = Daemon(cfg, repo=repo, gh=gh)
    _insert(repo, "bTST-GP-shadow", project)
    repo.update_card_fields("bTST-GP-shadow", {"work_type": "feature"})
    claim = _spawned(daemon, repo, paths, "bTST-GP-shadow", monkeypatch)
    _emit(claim, lambda wt: (wt / "anything").write_text("a\nb\nc\n", encoding="utf-8"))
    _exit(daemon, claim)
    logged = ev.read_events_for_card(paths, card_id="bTST-GP-shadow", tenant_id="default")
    shadow = [e for e in logged if e.kind == ev.KIND_GATE_SHADOW_DECISION]
    assert len(shadow) == 1, [e.kind for e in logged]
    inputs = shadow[0].payload["inputs"]
    flat = repr(inputs)
    assert "anything" in flat or any(isinstance(v, (int, float)) and v == 3 for v in inputs.values() if not isinstance(v, bool)), inputs


# G02 -----------------------------------------------------------------
def test_gap_end_job_closes_the_job_handle_once(tmp_path, monkeypatch):
    import os
    import sys
    if sys.platform != "win32":
        pytest.skip("Job Object")
    import _winapi
    from cards_runner.common.process_group import spawn_in_job
    proc = spawn_in_job([sys.executable, "-c", "pass"], cwd=str(tmp_path), env=dict(os.environ))
    proc.wait(timeout=30)
    closed: list[int] = []
    real_close = _winapi.CloseHandle
    monkeypatch.setattr(_winapi, "CloseHandle", lambda h: (closed.append(h), real_close(h))[1])
    proc.end_job()
    proc.end_job()
    assert len(closed) == 1, closed  # a second CloseHandle on a recycled handle value would hit a stranger


# P05 -----------------------------------------------------------------
def test_gap_emit_patch_does_not_climb_out_of_the_worker_clone(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    _g(parent, "init", "-q", "-b", "main")
    (parent / "root.txt").write_text("r\n")
    _g(parent, "add", "-A")
    _g(parent, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "p")
    base = _g(parent, "rev-parse", "HEAD").strip()
    wt = parent / "run" / "worktree"
    wt.mkdir(parents=True)
    (wt / "stray.txt").write_text("model deleted its .git\n")  # no .git here
    run_dir = tmp_path / "rd"
    run_dir.mkdir()
    ok = emit_patch(worktree=wt, base_sha=base, run_dir=run_dir)
    staged = _g(parent, "diff", "--cached", "--name-only").strip()
    assert not ok and staged == "", (ok, staged)


# S03 / S04 -----------------------------------------------------------
def test_gap_run_command_resolves_a_bare_program_name_with_an_empty_env(tmp_path):
    from cards_runner.common.sandbox import LocalProcessLauncher
    res = LocalProcessLauncher().run_command(["git", "--version"], cwd=tmp_path, env={}, timeout=30)
    assert res.returncode == 0 and "git version" in res.stdout, res


def test_gap_spawn_worker_keeps_stdout_and_stderr_apart(tmp_path):
    import os
    import sys
    from cards_runner.common.sandbox import LocalProcessLauncher
    out, err = tmp_path / "o.log", tmp_path / "e.log"
    with open(out, "wb") as o, open(err, "wb") as e:
        p = LocalProcessLauncher().spawn_worker(
            [sys.executable, "-c", "import sys;sys.stdout.write('OUT');sys.stderr.write('ERR')"],
            cwd=tmp_path, env=dict(os.environ), stdout=o, stderr=e)
        p.wait(timeout=30)
        p.end_job()
    assert out.read_text() == "OUT" and err.read_text() == "ERR"


# GS03 ----------------------------------------------------------------
def test_gap_removing_attempt_clones_drops_their_trust_records(setup, repo, paths, monkeypatch):
    project, gh, daemon = setup
    _insert(repo, "bTST-GP-forget", project)
    claim = _spawned(daemon, repo, paths, "bTST-GP-forget", monkeypatch)
    _emit(claim)
    seen: list[Path] = []
    real = git_safety.forget
    monkeypatch.setattr(git_safety, "forget", lambda p: (seen.append(Path(p)), real(p))[1])
    _exit(daemon, claim)
    names = {p.name for p in seen}
    assert {"integration", "verify"} <= names, names


# DT02 ----------------------------------------------------------------
def test_gap_a_launcher_oserror_fails_the_ac_instead_of_crashing(tmp_path):
    from cards_runner.verifier.handlers.deterministic import HandlerContext, handle_shell

    class Boom:
        def run_command(self, *a, **k):
            raise OSError("no shell")

    out = handle_shell({"command": "echo hi"}, HandlerContext(worktree=tmp_path, env={}, launcher=Boom()))
    assert out.passed is False and "could not spawn shell" in str(out.evidence)


# R01 -----------------------------------------------------------------
def test_gap_forensic_reaper_removes_a_run_dir_holding_a_real_clone(tmp_path):
    from cards_runner.daemon import reaper
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    run = tmp_path / "run"
    clones.prepare_worker_clone(project_dir=project, base_branch="main", branch="card/x/1",
                                dest=run / "worktree")
    (run / "worktree" / "new.txt").write_text("the worker's own work\n")  # git writes read-only objects
    _g(run / "worktree", "add", "-A")
    _g(run / "worktree", "-c", "user.email=m@example.test", "-c", "user.name=m", "commit", "-q", "-m", "w")
    assert reaper._remove_tree(run) is True and not run.exists()


# R02 + W01 (unit level) ----------------------------------------------
def test_gap_spawn_worker_uses_the_injected_launcher_and_passes_the_base_sha(tmp_path):
    from cards_runner.common.types import ClaimedCard
    from cards_runner.daemon import spawner
    seen: dict[str, Any] = {}

    class L:
        def spawn_worker(self, args, *, cwd, env, stdout, stderr):
            seen.update(env=env, cwd=cwd)
            return object()

        def run_command(self, *a, **k):  # pragma: no cover
            raise AssertionError

    run_dir = tmp_path / "run"
    claim = ClaimedCard(card_id="c", attempt_trace_id="a", trace_id="t", run_dir=run_dir,
                        worktree_path=tmp_path, card_file=run_dir / "card.md", base_sha="a" * 40)
    spawner.spawn_worker(cfg=DaemonConfig(todo_root=tmp_path), claim=claim, run_dir=run_dir, launcher=L())
    assert seen["env"]["CARDS_RUNNER_BASE_SHA"] == "a" * 40 and seen["cwd"] == tmp_path


# GS01 ----------------------------------------------------------------
def test_gap_an_hfs_ignorable_codepoint_alias_of_dot_git_is_refused(tmp_path):
    project = _project(tmp_path)
    git_safety.assert_trusted(project, first_sight_ok=True)
    path = ".g\u200cit/config"
    blob = subprocess.run([GIT, "hash-object", "--stdin"], input=b"evil\n", capture_output=True,
                          check=True).stdout.decode().strip()
    patch = (f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex {'0' * 40}..{blob}\n"
             f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+evil\n").encode("utf-8")
    pf = tmp_path / "h.patch"
    pf.write_bytes(patch)
    base = _g(project, "rev-parse", "main").strip()
    with pytest.raises(PatchRejected):
        integrate_patch(project_dir=project, base_branch="main", base_sha=base, branch="card/x/1",
                        patch_file=pf, dest=tmp_path / "int", message="m")


# R04 -----------------------------------------------------------------
def test_gap_the_obsolete_worktree_prune_flag_warns_and_is_ignored(repo, todo_root, store_spec, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        Daemon(DaemonConfig(todo_root=todo_root, store_spec=store_spec, skip_worktree=True,
                            worktree_prune_enabled=True), repo=repo)
    assert any("obsolete" in r.getMessage() for r in caplog.records)


# R05 -----------------------------------------------------------------
def test_gap_the_integration_commit_carries_the_attempt_trailer(
    repo, paths, todo_root, store_spec, tmp_path, monkeypatch,
):
    from tests.test_clone_per_worker import RecordingGh, _cfg

    class BodyGh(RecordingGh):
        def push(self, worktree, branch, *, set_upstream=True, sha=None):
            res = super().push(worktree, branch, set_upstream=set_upstream, sha=sha)
            self.calls[-1][1]["body"] = _g(worktree, "log", "-1", "--format=%B", sha)
            return res

    project = _project(tmp_path)
    gh = BodyGh()
    daemon = Daemon(_cfg(todo_root, store_spec), repo=repo, gh=gh)
    _insert(repo, "bTST-GP-trailer", project)
    claim = _spawned(daemon, repo, paths, "bTST-GP-trailer", monkeypatch)
    _emit(claim)
    _exit(daemon, claim)
    body = [a for n, a in gh.calls if n == "push"][0]["body"]
    assert f"Cards-Runner-Attempt: {claim.attempt_trace_id}" in body, body
