"""The .env loader at the CLI entry point, and the loud LLM degrade.

Two halves:
  * `common/envfile.py`: parsing, never overriding, never echoing a value,
    and the CARDS_RUNNER_ENV_FILE override (port of paradigm-provenance #7).
  * `daemon/llm_capability.py` plus its wiring: a daemon without a key says
    so at boot (WARNING) and in `cards-runner status`, and a stale record
    from an earlier daemon is never reported as the current one.
"""
from __future__ import annotations

import json
import logging
import os

import pytest

from cards_runner.cli.__main__ import main
from cards_runner.common.envfile import ENV_FILE_VAR, default_env_path, load_dotenv
from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon import llm_capability
from cards_runner.daemon.daemon import Daemon

FAKE = "sk-ant-test-fake-0000"
KEYS = ("CR_A", "CR_B", "CR_C", "CR_D", "CR_E", "CR_F", "CR_G", "CR_KEEP")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# --- loader ------------------------------------------------------------------


def test_parses_supported_formats(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_text(
        "# a comment\n"
        "\n"
        f"CR_A={FAKE}\n"
        f'CR_B="{FAKE} with space"\n'
        "CR_C='single # not a comment'\n"
        f"export CR_D={FAKE}\n"
        "CR_E=plain # trailing comment\n"
        "  CR_F = spaced  \n"
        'CR_G="$HOME/${X}"\n',
        encoding="utf-8",
    )
    assert load_dotenv(f) == 7
    assert os.environ["CR_A"] == FAKE
    assert os.environ["CR_B"] == f"{FAKE} with space"
    assert os.environ["CR_C"] == "single # not a comment"
    assert os.environ["CR_D"] == FAKE
    assert os.environ["CR_E"] == "plain"
    assert os.environ["CR_F"] == "spaced"
    assert os.environ["CR_G"] == "$HOME/${X}"  # no expansion


def test_does_not_override_existing(tmp_path, clean_env):
    clean_env.setenv("CR_KEEP", "from-shell")
    f = tmp_path / ".env"
    f.write_text(f"CR_KEEP={FAKE}\nCR_A={FAKE}\n", encoding="utf-8")
    assert load_dotenv(f) == 1
    assert os.environ["CR_KEEP"] == "from-shell"


def test_missing_file_is_noop(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == 0


def test_bom_is_tolerated(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_bytes(b"\xef\xbb\xbf" + f"CR_A={FAKE}\n".encode())
    assert load_dotenv(f) == 1
    assert os.environ["CR_A"] == FAKE


def test_malformed_lines_skipped_without_echo(tmp_path, clean_env, capsys, caplog):
    secret = "sk-ant-test-fake-9999"
    f = tmp_path / ".env"
    f.write_text(
        f"no equals sign {secret}\n"
        f"1BAD={secret}\n"
        f'CR_A="{secret}\n'
        f'CR_B="{secret}" junk\n'
        f"=={secret}\n"
        f"CR_C={FAKE}\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.DEBUG):
        assert load_dotenv(f) == 1
    out = capsys.readouterr()
    assert secret not in out.out + out.err
    assert secret not in caplog.text


def test_default_path_is_cwd_unless_overridden(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_FILE_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_env_path() == tmp_path / ".env"
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / "elsewhere.env"))
    assert default_env_path() == tmp_path / "elsewhere.env"


def test_cli_main_loads_the_env_file_before_the_key_check(tmp_path, monkeypatch, capsys):
    # The whole point: with the key only in the .env, `start --invoker sdk`
    # gets past the "needs ANTHROPIC_API_KEY" refusal. Daemon.run is stubbed
    # so nothing boots.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / "runner.env"
    env.write_text(f"ANTHROPIC_API_KEY={FAKE}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FILE_VAR, str(env))
    monkeypatch.setattr(Daemon, "run", lambda self: 0)
    rc = main(["start", "--todo-root", str(tmp_path / "todo"), "--invoker", "sdk"])
    assert rc == 0, capsys.readouterr().err
    assert os.environ["ANTHROPIC_API_KEY"] == FAKE
    assert FAKE not in capsys.readouterr().out


def test_cli_main_without_any_key_still_refuses_sdk(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(Daemon, "run", lambda self: 0)
    rc = main(["start", "--todo-root", str(tmp_path / "todo"), "--invoker", "sdk"])
    assert rc == 2
    assert ENV_FILE_VAR in capsys.readouterr().err


# --- capability --------------------------------------------------------------


def test_detect_reports_missing_key_with_a_reason():
    cap = llm_capability.detect({})
    assert cap.key_present is False and cap.live is False
    assert "ANTHROPIC_API_KEY" in (cap.reason or "")
    assert llm_capability.detect({"ANTHROPIC_API_KEY": ""}).key_present is False


def test_warning_names_every_degraded_feature():
    text = llm_capability.warning_text(llm_capability.LlmCapability(False, True))
    assert "DEGRADED" in text
    for feature in llm_capability.DEGRADED_FEATURES:
        assert feature in text
    assert llm_capability.warning_text(llm_capability.LlmCapability(True, True)) == ""


def test_record_never_holds_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE)
    rec = llm_capability.to_record(llm_capability.detect(), pid=123, now=1.0)
    blob = json.dumps(rec)
    assert FAKE not in blob and FAKE[:8] not in blob
    assert rec["anthropic_key_present"] is True


def test_stale_record_is_not_the_running_daemon(tmp_path):
    path = tmp_path / "caps.json"
    llm_capability.write_record(
        path, llm_capability.to_record(llm_capability.LlmCapability(True, True), pid=111)
    )
    assert llm_capability.read_for_daemon(path, 111)["llm_reviews"] == "live"
    assert llm_capability.read_for_daemon(path, 222) is None  # a different daemon
    assert llm_capability.read_for_daemon(path, None) is None  # no daemon running
    path.write_text("{not json", encoding="utf-8")
    assert llm_capability.read_for_daemon(path, 111) is None


def test_daemon_boot_warns_and_records_when_key_missing(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    daemon = Daemon(DaemonConfig(todo_root=tmp_path / "todo", store_spec=f"sqlite:{tmp_path / 'c.db'}"))
    with caplog.at_level(logging.WARNING, logger="cards_runner.daemon.daemon"):
        cap = daemon._report_llm_capability()
    assert cap.live is False
    assert "LLM reviews DEGRADED" in caplog.text
    rec = json.loads(RuntimePaths.from_root(tmp_path / "todo").capabilities.read_text(encoding="utf-8"))
    assert rec["llm_reviews"] == "degraded"
    assert rec["pid"] == os.getpid()


def test_status_reports_the_running_daemons_record(tmp_path, monkeypatch, capsys):
    root = tmp_path / "todo"
    paths = RuntimePaths.from_root(root)
    paths.ensure()
    llm_capability.write_record(
        paths.capabilities,
        llm_capability.to_record(llm_capability.LlmCapability(False, True), pid=4242),
    )
    import cards_runner.cli.__main__ as cli

    monkeypatch.setattr(cli.FileLock, "read_pid", lambda self: 4242)
    monkeypatch.setattr(cli, "pid_alive", lambda pid: True)
    store = f"sqlite:{tmp_path / 'c.db'}"
    assert main(["status", "--todo-root", str(root), "--store", store, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["llm_reviews"] == "degraded"
    assert "ANTHROPIC_API_KEY" in payload["llm_reviews_reason"]
    # The same file, but the running daemon is a different process: unknown.
    monkeypatch.setattr(cli.FileLock, "read_pid", lambda self: 9999)
    assert main(["status", "--todo-root", str(root), "--store", store]) == 0
    assert "llm reviews: unknown" in capsys.readouterr().out


def test_real_boot_writes_the_record(tmp_path, monkeypatch):
    # Drives the actual `_boot()`, so deleting its call to
    # `_report_llm_capability` fails here rather than passing silently.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    root = tmp_path / "todo"
    daemon = Daemon(DaemonConfig(todo_root=root, store_spec=f"sqlite:{tmp_path / 'c.db'}"))
    try:
        daemon._boot()
    finally:
        daemon.close()
    rec = llm_capability.read_for_daemon(RuntimePaths.from_root(root).capabilities, os.getpid())
    assert rec is not None and rec["llm_reviews"] == "degraded"