"""`start --invoker sdk` without ANTHROPIC_API_KEY, for an all-local tier map.

The key may be absent only when nothing would call Anthropic. Both
directions are pinned: an all-local map starts with no key, and a map with
even one Anthropic tier still refuses, naming that tier. The spawner half
checks the worker receives the same tier-map path the CLI checked; without
it the scrub drops the variable and the worker loads the Claude map.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from cards_runner.cli.__main__ import _cmd_start
from cards_runner.common.canonical_config import ENV_TIER_MAP_PATH
from cards_runner.common.types import ClaimedCard, DaemonConfig
from cards_runner.daemon import spawner
from cards_runner.providers.registry import base_url_problem, tiers_needing_a_key


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

def test_tiers_needing_a_key_follows_provider_routing() -> None:
    hits = tiers_needing_a_key({
        1: "ollama/qwen3:30b", 2: "vllm/x", 3: "claude-sonnet-4-6",
        4: "anthropic/claude-opus", 5: "local/y", 6: "openai/gpt",
    })
    assert hits == [
        (3, "claude-sonnet-4-6", "anthropic"),
        (4, "anthropic/claude-opus", "anthropic"),
        (6, "openai/gpt", "openai"),
    ]


def test_tiers_needing_a_key_empty_for_keyless_local() -> None:
    assert tiers_needing_a_key({1: "ollama/a", 2: "vllm/b", 3: "local/c"}) == []


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
    assert "tier 5 (claude-opus-4-7, provider anthropic)" in err
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


# ---- deep verify of #84: W2 (relative path), W1 (credentials), W3, W4 -------

def test_a_relative_tier_map_is_pinned_absolute_for_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig],
) -> None:
    # W2: the worker runs in the card worktree, not the CLI cwd. A relative
    # path would miss there and fall through to the repo-root Claude map.
    cli_cwd = tmp_path / "cli"
    (cli_cwd / "maps").mkdir(parents=True)
    _tier_map(cli_cwd / "maps", ALL_LOCAL)
    monkeypatch.chdir(cli_cwd)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, "maps/tier_map.yaml")
    assert _cmd_start(_args()) == 0
    pinned = Path(os.environ[ENV_TIER_MAP_PATH])
    assert pinned.is_absolute()
    monkeypatch.chdir(tmp_path)  # anywhere else, as the worker would be
    assert pinned.is_file()


def test_a_relative_tier_map_reaches_the_spawned_worker_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig],
) -> None:
    cli_cwd = tmp_path / "cli"
    (cli_cwd / "maps").mkdir(parents=True)
    _tier_map(cli_cwd / "maps", ALL_LOCAL)
    monkeypatch.chdir(cli_cwd)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, "maps/tier_map.yaml")
    assert _cmd_start(_args()) == 0
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.chdir(worktree)
    env = _spawn_env(tmp_path, monkeypatch)
    assert Path(env[ENV_TIER_MAP_PATH]).is_absolute()
    assert Path(env[ENV_TIER_MAP_PATH]).is_file()


@pytest.mark.parametrize("with_key", [False, True])
def test_a_tier_map_path_naming_no_file_is_refused(
    with_key: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    # With a key this was the silent case: the miss fell through to Claude.
    if with_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(tmp_path / "missing.yaml"))
    assert _cmd_start(_args()) == 2
    assert patched_daemon == []
    assert "not a file" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pw@ollama.invalid:11434/v1",
        "http://user@ollama.invalid:11434/v1",
        "http://ollama.invalid:11434/v1?api_key=abc",
        "http://ollama.invalid:11434/v1?x=1&Token=abc",
        "ftp://ollama.invalid/v1",
    ],
)
def test_a_base_url_carrying_a_credential_is_refused(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    monkeypatch.setenv("CARDS_RUNNER_OLLAMA_BASE_URL", url)
    assert _cmd_start(_args()) == 2
    err = capsys.readouterr().err
    assert "CARDS_RUNNER_OLLAMA_BASE_URL" in err
    # X1: the var is named, no part of the URL is echoed (not even the host)
    assert "abc" not in err and "pw" not in err and "ollama.invalid" not in err and "11434" not in err


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434/v1", "https://ollama.local:8443/v1/", "http://[::1]:8000/v1", "http://gpu-box"],
)
def test_a_plain_local_endpoint_is_allowed(url: str) -> None:
    assert base_url_problem(url) is None


@pytest.mark.parametrize(
    "url",
    [
        # X2: an allowlist, so these go regardless of the parameter's name
        "http://127.0.0.1:11434/v1?timeout=30",
        "http://127.0.0.1:11434/v1?jwt=eyJabc",
        "http://127.0.0.1:11434/v1?bearer=abc",
        "http://127.0.0.1:11434/v1?sv=2024&sig=abc",
        "http://127.0.0.1:11434/v1#tok=abc",
        "http://127.0.0.1:11434/v1;sas=abc",
        "http://:pw@127.0.0.1/v1",
        "http://127.0.0.1:notaport/v1",
        "http://127.0.0.1/v1?",
    ],
)
def test_anything_beyond_host_port_path_is_refused(url: str) -> None:
    assert base_url_problem(url) is not None


def test_the_spawner_drops_a_credentialed_url_even_if_the_cli_was_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
) -> None:
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    monkeypatch.setenv("CARDS_RUNNER_OLLAMA_BASE_URL", "http://u:secretpw@ollama.invalid/v1")
    env = _spawn_env(tmp_path, monkeypatch)
    assert "CARDS_RUNNER_OLLAMA_BASE_URL" not in env
    assert all("secretpw" not in v for v in env.values())


def test_every_offending_tier_is_named_not_just_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    # W3: mutant M8 named only the first hit and survived the 1-tier test.
    mixed = {**ALL_LOCAL, 3: "claude-sonnet-4-6", 6: "claude-opus-4-7"}
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, mixed)))
    assert _cmd_start(_args()) == 2
    err = capsys.readouterr().err
    assert "tier 3 (claude-sonnet-4-6" in err
    assert "tier 6 (claude-opus-4-7" in err


@pytest.mark.parametrize("model", ["openai/gpt-x", "gemini/g-1", "azure/dep"])
def test_a_keyed_non_anthropic_provider_is_not_keyless(
    model: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    # W4: these pass a "not Anthropic" test but need a key the spawner
    # never forwards, so they would fail at the first call.
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, {**ALL_LOCAL, 2: model})))
    assert _cmd_start(_args()) == 2
    assert f"tier 2 ({model}" in capsys.readouterr().err


def _spawn_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    seen: dict[str, str] = {}

    def fake_spawn(args: list[str], *, env: dict[str, str], **_: object) -> object:
        seen.update(env)
        return object()

    monkeypatch.setattr(spawner, "spawn_in_job", fake_spawn)
    run_dir = tmp_path / "run-spawn"
    claim = ClaimedCard(
        card_id="c2", attempt_trace_id="a2", trace_id="t2", run_dir=run_dir,
        worktree_path=tmp_path, card_file=run_dir / "card.md",
    )
    spawner.spawn_worker(
        cfg=DaemonConfig(todo_root=tmp_path, invoker="sdk"), claim=claim, run_dir=run_dir,
    )
    return seen

# ---- deep verify of #84 r2: X3 (pin at worker load), X4, X5 ----------------

def test_a_set_tier_map_var_is_a_pin_at_worker_load_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # X3: deleted or broken AFTER start, the worker must fail, not load Claude.
    from cards_runner.common.canonical_config import CanonicalConfigMissing, load_tier_map

    path = _tier_map(tmp_path, ALL_LOCAL)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(path))
    assert load_tier_map().model_for(1) == "ollama/qwen3:30b"
    path.write_text("tiers: [this is not a mapping\n", encoding="utf-8")
    with pytest.raises(CanonicalConfigMissing):
        load_tier_map()
    path.unlink()
    with pytest.raises(CanonicalConfigMissing):
        load_tier_map()


def test_an_unset_tier_map_var_still_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from cards_runner.common.canonical_config import load_tier_map

    monkeypatch.delenv(ENV_TIER_MAP_PATH, raising=False)
    assert load_tier_map().tiers  # repo-root or embedded map, no raise


def test_a_broken_pinned_map_is_refused_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    patched_daemon: list[DaemonConfig], capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: 1\n", encoding="utf-8")  # no tiers section
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(broken))
    assert _cmd_start(_args()) == 2
    assert patched_daemon == []
    assert ENV_TIER_MAP_PATH in capsys.readouterr().err


def test_spawn_worker_absolutizes_a_relative_map_path_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_key: None,
) -> None:
    # X5: a direct caller of spawn_worker, with no CLI in front of it.
    (tmp_path / "maps").mkdir()
    _tier_map(tmp_path / "maps", ALL_LOCAL)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_TIER_MAP_PATH, "maps/tier_map.yaml")
    env = _spawn_env(tmp_path, monkeypatch)
    assert Path(env[ENV_TIER_MAP_PATH]) == (tmp_path / "maps" / "tier_map.yaml").resolve()


def test_the_worker_passthrough_set_is_exactly_this(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # X4: pins the set. Adding GH_TOKEN, an *_API_KEY, or any new name to
    # what the worker receives must be a deliberate change to this test.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    for name in (
        "CARDS_RUNNER_CASCADE_THRESHOLD", "CARDS_RUNNER_MAX_OUTPUT_TOKENS",
        "CARDS_RUNNER_MAX_ESCALATIONS", "CARDS_RUNNER_PRICING_JSON",
        "CARDS_RUNNER_USE_TOOLS", "CARDS_RUNNER_MAX_TOOL_TURNS",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv(ENV_TIER_MAP_PATH, str(_tier_map(tmp_path, ALL_LOCAL)))
    for name in ("CARDS_RUNNER_OLLAMA_BASE_URL", "CARDS_RUNNER_VLLM_BASE_URL", "CARDS_RUNNER_LOCAL_BASE_URL"):
        monkeypatch.setenv(name, "http://127.0.0.1:11434/v1")
    # decoys that must never pass
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY",
                 "CARDS_RUNNER_OLLAMA_API_KEY", "CARDS_RUNNER_OPENAI_BASE_URL",
                 "CARDS_RUNNER_SOMETHING_NEW", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "decoy-not-a-real-secret")
    env = _spawn_env(tmp_path, monkeypatch)
    assert {k for k in env if k.startswith("CARDS_RUNNER_") or k.startswith("ANTHROPIC_")} == {
        "CARDS_RUNNER_CARD_PATH", "CARDS_RUNNER_WORKTREE", "CARDS_RUNNER_ATTEMPT_TRACE_ID",
        "CARDS_RUNNER_TRACE_ID", "CARDS_RUNNER_HEARTBEAT_INTERVAL_SEC",
        "CARDS_RUNNER_STUB_SLEEP_SEC", "CARDS_RUNNER_RUN_DIR", "CARDS_RUNNER_INVOKER",
        "ANTHROPIC_API_KEY",
        "CARDS_RUNNER_CASCADE_THRESHOLD", "CARDS_RUNNER_MAX_OUTPUT_TOKENS",
        "CARDS_RUNNER_MAX_ESCALATIONS", "CARDS_RUNNER_PRICING_JSON",
        "CARDS_RUNNER_USE_TOOLS", "CARDS_RUNNER_MAX_TOOL_TURNS",
        ENV_TIER_MAP_PATH,
        "CARDS_RUNNER_OLLAMA_BASE_URL", "CARDS_RUNNER_VLLM_BASE_URL", "CARDS_RUNNER_LOCAL_BASE_URL",
    }
    assert "decoy-not-a-real-secret" not in env.values()