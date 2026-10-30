"""`--on-local-map-unavailable stop|warn`: what a card does when the pinned
local tier map is missing or broken.

`stop` (default) fails closed, exactly as #84 shipped. `warn` runs the card on
the hosted (paid) map, but ONLY with a key set at start, and never quietly: a
start banner and a per-card warning name the paid model, the model is written
onto the card, and a `map_fallback` event is logged. Each test pins one
direction against its opposite.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import pytest

from cards_runner.cli.__main__ import _cmd_start
from cards_runner.common.canonical_config import ENV_TIER_MAP_PATH, load_tier_map
from cards_runner.common.types import ClaimedCard, DaemonConfig
from cards_runner.daemon import spawner
from cards_runner.daemon.daemon import Daemon
from cards_runner.providers.registry import split_model

ALL_LOCAL = {p: "ollama/qwen3:30b" for p in range(1, 7)}


def _tier_map(tmp_path: Path, models: dict[int, str]) -> Path:
    lines = ["version: 1", "tiers:"]
    for points, model in models.items():
        lines += [f"  {points}:", f"    model: {model}"]
    path = tmp_path / "tier_map.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def gone_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A pinned local map that has been deleted."""
    path = _tier_map(tmp_path, ALL_LOCAL)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(path))
    path.unlink()
    return path


def _hosted_model(points: int) -> str:
    return load_tier_map(honor_pin=False).model_for(points)


# ---- the per-card decision ---------------------------------------------------


def test_no_pin_or_a_loading_pin_never_falls_back(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    monkeypatch.delenv(ENV_TIER_MAP_PATH, raising=False)
    assert spawner.plan_worker_map("warn", 2).fallback is False
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    assert spawner.plan_worker_map("warn", 2) == spawner.MapPlan(False)


@pytest.mark.parametrize("mode,key,falls_back", [
    ("stop", True, False), ("stop", False, False), ("warn", False, False), ("warn", True, True),
])
def test_only_warn_with_a_key_falls_back(mode: str, key: bool, falls_back: bool, gone_map: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plan = spawner.plan_worker_map(mode, 3)
    assert plan.fallback is falls_back
    assert plan.reason  # the unavailability is always named
    if falls_back:
        assert plan.hosted_model == _hosted_model(3)
        assert split_model(plan.hosted_model)[0] == "anthropic"  # a paid model, named


def test_a_broken_pin_counts_as_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    broken = tmp_path / "broken.yaml"
    broken.write_text("tiers: [not, a, mapping\n", encoding="utf-8")
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(broken))
    assert spawner.plan_worker_map("warn", 1).fallback is True


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError):
        spawner.plan_worker_map("maybe", 1)


# ---- what the worker actually receives ---------------------------------------


def _spawn_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
               plan: spawner.MapPlan | None) -> dict[str, str]:
    seen: dict[str, str] = {}

    class FakeLauncher:
        """Since #87 the worker starts through the SandboxLauncher seam."""

        def spawn_worker(self, args: list[str], *, env: dict[str, str], **_: object) -> object:
            seen.update(env)
            return object()

    run_dir = tmp_path / "run"
    claim = ClaimedCard(card_id="c1", attempt_trace_id="a1", trace_id="t1", run_dir=run_dir,
                        worktree_path=tmp_path, card_file=run_dir / "card.md")
    spawner.spawn_worker(cfg=DaemonConfig(todo_root=tmp_path, invoker="sdk"), claim=claim,
                         run_dir=run_dir, map_plan=plan, launcher=FakeLauncher())
    return seen


def test_a_fallback_worker_gets_the_key_and_no_pin(gone_map: Path, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    plan = spawner.plan_worker_map("warn", 2)
    env = _spawn_env(tmp_path, monkeypatch, plan)
    assert ENV_TIER_MAP_PATH not in env  # so it loads the hosted map plan_worker_map named
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test-0042"


def test_under_stop_the_worker_keeps_the_pin_and_gets_no_key(
    gone_map: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Control for the test above, and #84's shipped behaviour.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    env = _spawn_env(tmp_path, monkeypatch, spawner.plan_worker_map("stop", 2))
    assert env[ENV_TIER_MAP_PATH] == str(gone_map.resolve())
    assert "ANTHROPIC_API_KEY" not in env


# ---- the daemon: per-card warning, the model on the card, an event -----------


def _claim_and_spawn(mode: str, repo: Any, card_factory: Any, daemon_cfg: DaemonConfig,
                     monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any]]:
    from dataclasses import replace

    plans: list[Any] = []
    monkeypatch.setattr("cards_runner.daemon.daemon.spawn_worker",
                        lambda **kw: (plans.append(kw.get("map_plan")), object())[1])
    cid = card_factory("bTST-MAP-01")
    daemon = Daemon(replace(daemon_cfg, invoker="sdk", on_local_map_unavailable=mode), repo=repo)
    claim = daemon._try_claim(repo.get_card(cid))
    assert claim is not None and daemon._prepare_and_spawn(claim) is True
    return repo.get_card(cid), plans


def test_warn_names_the_paid_model_on_the_card_in_an_event_and_the_log(
    gone_map: Path, repo: Any, card_factory: Any, daemon_cfg: DaemonConfig,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    with caplog.at_level(logging.WARNING):
        card, plans = _claim_and_spawn("warn", repo, card_factory, daemon_cfg, monkeypatch)
    model = _hosted_model(card.points or 1)
    assert plans[0].fallback and plans[0].hosted_model == model
    assert card.field_value("billed_fallback_model") == model
    assert card.field_value("tier_map_fallback_reason")
    events = [e for e in repo.list_events(card.card_id) if e.type == "map_fallback"]
    assert len(events) == 1 and events[0].payload["billed_model"] == model
    assert any("PAID MODEL" in r.message and model in r.message for r in caplog.records)


def test_stop_records_no_fallback(gone_map: Path, repo: Any, card_factory: Any,
                                  daemon_cfg: DaemonConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    card, plans = _claim_and_spawn("stop", repo, card_factory, daemon_cfg, monkeypatch)
    assert plans[0].fallback is False
    assert card.field_value("billed_fallback_model") is None
    assert not [e for e in repo.list_events(card.card_id) if e.type == "map_fallback"]


# ---- start: refuse, or start with a banner ----------------------------------


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
        project_config_path=None, on_local_map_unavailable="stop",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("mode,key,rc", [
    ("stop", True, 2), ("stop", False, 2), ("warn", False, 2), ("warn", True, 0),
])
def test_start_with_the_map_gone(mode: str, key: bool, rc: int, gone_map: Path,
                                 monkeypatch: pytest.MonkeyPatch, patched_daemon: list[DaemonConfig],
                                 capsys: pytest.CaptureFixture[str]) -> None:
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _cmd_start(_args(on_local_map_unavailable=mode)) == rc
    err = capsys.readouterr().err
    if rc == 0:
        assert "UNAVAILABLE" in err and "PAID" in err and _hosted_model(1) in err
        assert patched_daemon[0].on_local_map_unavailable == "warn"
    else:
        assert patched_daemon == [] and "not a file" in err
        if mode == "warn":
            assert "only when ANTHROPIC_API_KEY is set" in err


def test_warn_with_a_healthy_map_still_says_what_would_happen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patched_daemon: list[DaemonConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    assert _cmd_start(_args(on_local_map_unavailable="warn")) == 0
    err = capsys.readouterr().err
    assert "NOTICE" in err and "PAID" in err
    assert _cmd_start(_args(on_local_map_unavailable="stop")) == 0
    assert "PAID" not in capsys.readouterr().err  # control: stop prints nothing


def test_a_credentialed_base_url_is_refused_even_under_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patched_daemon: list[DaemonConfig],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(tmp_path / "gone.yaml"))
    monkeypatch.setenv("CARDS_RUNNER_OLLAMA_BASE_URL", "http://user:pw@ollama.invalid:11434")
    assert _cmd_start(_args(on_local_map_unavailable="warn")) == 2
    assert patched_daemon == []
