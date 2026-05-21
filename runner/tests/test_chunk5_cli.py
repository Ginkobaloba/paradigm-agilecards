"""Tests for the chunk-5 CLI flags.

The CLI surface itself does very little; these tests verify that the
new flags end up on the constructed `DaemonConfig` and pass through the
defaults correctly when the flag is absent.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cards_runner.cli.__main__ import _cmd_start
from cards_runner.common.types import DaemonConfig


@pytest.fixture
def patched_daemon(monkeypatch: pytest.MonkeyPatch) -> list[DaemonConfig]:
    captured: list[DaemonConfig] = []

    class _NoRunDaemon:
        def __init__(self, cfg: DaemonConfig) -> None:
            captured.append(cfg)

        def run(self) -> int:
            return 0

    monkeypatch.setattr(
        "cards_runner.cli.__main__.Daemon", _NoRunDaemon
    )
    return captured


def _build_args(**overrides: object) -> argparse.Namespace:
    base = dict(
        todo_root=Path("/tmp/td"),
        store="",
        poll_interval_sec=5.0,
        max_parallel=4,
        orphan_timeout_minutes=120,
        heartbeat_interval_sec=30.0,
        stub_sleep_sec=3.0,
        invoker="stub",
        skip_worktree=True,
        no_verifier=False,
        pr_gate=False,
        gh_path=None,
        git_path=None,
        auto_merge_strategy=None,
        pr_base_branch_default=None,
        no_boot_alive_check=False,
        forensic_ttl_hours=None,
        pr_unblock=False,
        sibling_reviewer=False,
        amendment_reviewer=False,
        worktree_prune=False,
        worktree_prune_interval_sec=None,
        project_config_path=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_defaults_match_daemonconfig_defaults(
    patched_daemon: list[DaemonConfig]
) -> None:
    args = _build_args()
    rc = _cmd_start(args)
    assert rc == 0
    cfg = patched_daemon[0]
    assert cfg.pr_gate_enabled is False
    assert cfg.pr_unblock_enabled is False
    assert cfg.sibling_reviewer_enabled is False
    assert cfg.amendment_reviewer_enabled is False
    assert cfg.worktree_prune_enabled is False
    assert cfg.boot_worker_alive_check is True
    assert cfg.gh_path == "gh"
    assert cfg.git_path == "git"
    assert cfg.auto_merge_strategy == "squash"


def test_pr_gate_flag_sets_config(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(pr_gate=True, pr_unblock=True))
    cfg = patched_daemon[0]
    assert cfg.pr_gate_enabled is True
    assert cfg.pr_unblock_enabled is True


def test_reviewer_flags_pass_through(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(sibling_reviewer=True, amendment_reviewer=True))
    cfg = patched_daemon[0]
    assert cfg.sibling_reviewer_enabled is True
    assert cfg.amendment_reviewer_enabled is True


def test_gh_path_override(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(gh_path="/opt/gh"))
    assert patched_daemon[0].gh_path == "/opt/gh"


def test_no_boot_alive_check_flag(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(no_boot_alive_check=True))
    assert patched_daemon[0].boot_worker_alive_check is False


def test_forensic_ttl_override(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(forensic_ttl_hours=48))
    assert patched_daemon[0].worktree_forensic_ttl_hours == 48


def test_worktree_prune_flags(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(
        worktree_prune=True, worktree_prune_interval_sec=120
    ))
    cfg = patched_daemon[0]
    assert cfg.worktree_prune_enabled is True
    assert cfg.worktree_prune_interval_sec == 120


def test_auto_merge_strategy_override(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(auto_merge_strategy="rebase"))
    assert patched_daemon[0].auto_merge_strategy == "rebase"


def test_project_config_path_override(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(project_config_path=Path("/etc/cards/project.yaml")))
    assert patched_daemon[0].project_config_path == Path("/etc/cards/project.yaml")


def test_pr_base_branch_override(patched_daemon: list[DaemonConfig]) -> None:
    _cmd_start(_build_args(pr_base_branch_default="develop"))
    assert patched_daemon[0].pr_base_branch_default == "develop"


def test_unknown_invoker_with_no_key_errors(
    patched_daemon: list[DaemonConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = _cmd_start(_build_args(invoker="sdk"))
    assert rc == 2
    assert patched_daemon == []  # daemon never constructed.
