"""`start --invoker sdk` without ANTHROPIC_API_KEY, for an all-local tier map.

The key may be absent only when nothing would call Anthropic. Both
directions are pinned: an all-local map starts with no key, and a map with
even one Anthropic tier still refuses, naming that tier. The spawner half
checks the worker receives the same tier-map path the CLI checked; without
it the scrub drops the variable and the worker loads the Claude map.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cards_runner.cli.__main__ import _cmd_start
from cards_runner.common.canonical_config import ENV_TIER_MAP_PATH
from cards_runner.common.types import ClaimedCard, DaemonConfig
from cards_runner.daemon import spawner
from cards_runner.providers.registry import anthropic_tiers


@pytest.fixture
def patched_daemon(monkeypatch: pytest.MonkeyPatch) -> list[DaemonConfig]:
    captured: list[DaemonConfig] = []

    class _NoRunDaemon:
        def __init__(self, cfg: DaemonConfig) -> None:
            captured.append(cfg)

        def run(self) -> int:
            return 0

    monkeypatch.setattr("cards_runner.cli.__main__.Daemon", _NoRunDaemon)
    return captured


def _args(**overrides: object) -> argparse.Namespace:
    base = dict(
        todo_root=Path("/tmp/td"), store="", poll_interval_sec=5.0,
        max_parallel=4, orphan_timeout_minutes=120,
        heartbeat_interval_sec=30.0, stub_sleep_sec=3.0, invoker="sdk",
        skip_worktree=True, no_verifier=False, pr_gate=False, gh_path=None,
        git_path=None, auto_merge_strategy=None, pr_base_branch_default=None,
        no_boot_alive_check=False, forensic_ttl_hours=None, pr_unblock=False,
        sibling_reviewer=False, amendment_reviewer=False,
        worktree_prune=False, worktree_prune_interval_sec=None,
        project_config_path=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _tier_map(tmp_path: Path, models: dict[int, str]) -> Path:
    lines = ["version: 1", "tiers:"]
    for points, model in models.items():
        lines += [f"  {points}:", f"    model: {model}"]
    path = tmp_path / "tier_map.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


ALL_LOCAL = {p: "ollama/qwen3:30b" for p in range(1, 7)}


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---- which tiers count as Anthropic -----------------------------------------

def test_anthropic_tiers_follows_provider_routing() -> None:
    hosted = anthropic_tiers({
        1: "ollama/qwen3:30b", 2: "vllm/x", 3: "claude-sonnet-4-6",
        4: "anthropic/claude-opus", 5: "claude/claude-haiku", 6: "openai/gpt",
    })
    assert hosted == [
        (3, "claude-sonnet-4-6"), (4, "anthropic/claude-opus"),
        (5, "claude/claude-haiku"),
    ]


def test_anthropic_tiers_empty_for_all_local() -> None:
    assert anthropic_tiers(ALL_LOCAL) == []


# ---- the CLI refusal, both directions --------------------------------------

def test_all_local_map_starts_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig],
) -> None:
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    assert _cmd_start(_args()) == 0
    assert len(patched_daemon) == 1


def test_one_anthropic_tier_still_refuses_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    mixed = {**ALL_LOCAL, 5: "claude-opus-4-7"}
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, mixed)))
    assert _cmd_start(_args()) == 2
    assert patched_daemon == []
    err = capsys.readouterr().err
    assert "tier 5 (claude-opus-4-7)" in err
    assert "tier 1" not in err  # only the offending tier is named


def test_default_claude_map_still_refuses(
    monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig],
) -> None:
    monkeypatch.delenv(ENV_TIER_MAP_PATH, raising=False)
    assert _cmd_start(_args()) == 2
    assert patched_daemon == []


def test_sdk_tools_needs_the_key_even_for_an_all_local_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    assert _cmd_start(_args(invoker="sdk-tools")) == 2
    assert "tool loop is Anthropic-only" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["sibling_reviewer", "amendment_reviewer"])
def test_a_claude_reviewer_needs_the_key(
    flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    assert _cmd_start(_args(**{flag: True})) == 2
    assert f"--{flag.replace('_', '-')}" in capsys.readouterr().err


def test_with_a_key_a_mixed_map_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    patched_daemon: list[DaemonConfig],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    mixed = {**ALL_LOCAL, 5: "claude-opus-4-7"}
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, mixed)))
    assert _cmd_start(_args()) == 0


# ---- the worker sees the map the CLI checked -------------------------------

def test_spawner_passes_the_tier_map_and_local_endpoint_not_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
) -> None:
    seen: dict[str, str] = {}

    def fake_spawn(args: list[str], *, env: dict[str, str], **_: object) -> object:
        seen.update(env)
        return object()

    monkeypatch.setattr(spawner, "spawn_in_job", fake_spawn)
    map_path = _tier_map(tmp_path, ALL_LOCAL)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(map_path))
    monkeypatch.setenv("CARDS_RUNNER_OLLAMA_BASE_URL", "http://ollama.invalid:11434/v1")
    monkeypatch.setenv("CARDS_RUNNER_OLLAMA_API_KEY", "not-for-the-worker")
    run_dir = tmp_path / "run"
    claim = ClaimedCard(
        card_id="c1", attempt_trace_id="a1", trace_id="t1", run_dir=run_dir,
        worktree_path=tmp_path, card_file=run_dir / "card.md",
    )
    spawner.spawn_worker(
        cfg=DaemonConfig(todo_root=tmp_path, invoker="sdk"), claim=claim,
        run_dir=run_dir,
    )
    assert seen[ENV_TIER_MAP_PATH] == str(map_path)
    assert seen["CARDS_RUNNER_OLLAMA_BASE_URL"] == "http://ollama.invalid:11434/v1"
    assert "CARDS_RUNNER_OLLAMA_API_KEY" not in seen
    assert "ANTHROPIC_API_KEY" not in seen
