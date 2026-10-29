"""The scorer, with fake models. No GPU, no Ollama.

The first test is the scorer's own positive control: a model that answers
every card with its reference solution must pass all ten exam cards and fail
both negative controls, through the real verifier and real pytest.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

import run_bakeoff as rb


def _blocks(files: dict[str, str]) -> str:
    out = ["Here are the files."]
    for path, content in files.items():
        out += [f"<<<FILE {path}>>>", content.rstrip("\n"), "<<<END FILE>>>"]
    return "\n".join(out) + "\n"


def _solution(card_id: str) -> dict[str, str]:
    root = rb.SOLUTIONS / card_id
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*")) if p.is_file()}


class FakeClient:
    def __init__(self, answer, name: str = "fake") -> None:  # noqa: ANN001
        self.name = name
        self.answer = answer
        self.calls: list[str] = []
        self.loaded = self.unloaded = 0

    def load(self) -> dict[str, Any]:
        self.loaded += 1
        return {"wall_seconds": 0.0}

    def chat(self, system: str, user: str) -> rb.Reply:
        self.calls.append(user)
        now = time.time()
        return rb.Reply(self.answer(user), now, now + 0.01)

    def unload(self) -> None:
        self.unloaded += 1


def _card_of(user: str, cards: list[rb.Card]) -> rb.Card:
    return next(c for c in cards if c.body.rstrip("\n") in user)


class NoVram(rb.VramSampler):
    def read(self) -> int | None:
        self.available = False
        return None


def _run(tmp_path: Path, answer, **kw: Any) -> dict[str, Any]:  # noqa: ANN001
    return rb.run_arm(arm="T", client=FakeClient(answer), runs=kw.pop("runs", 1),
                      out_dir=tmp_path / "out", vram=NoVram(), probe=False, **kw)


def test_reference_solutions_pass_the_exam_and_never_the_controls(tmp_path: Path) -> None:
    cards = rb.load_cards()
    assert len(cards) == 12
    summary = _run(tmp_path, lambda user: _blocks(_solution(_card_of(user, cards).card_id)))
    records = [json.loads(line) for line in (tmp_path / "out" / "runs.jsonl").read_text().splitlines()]
    by_id = {r["card_id"]: r for r in records}
    failing = [cid for cid, r in by_id.items() if cid not in rb.NEGATIVE_CONTROLS and not r["passed"]]
    assert failing == [], {cid: by_id[cid]["ac"] for cid in failing}
    assert summary["pass_at_1"] == 1.0 and summary["pass_pow_3"] == 1.0
    assert summary["controls"] == {"runs": 2, "passes": 0} and summary["arm_valid"]
    assert by_id["bk-09"]["touched_tests"] == ["tests/test_dates.py"]  # discarded by the restore
    assert summary["suspect"] == []


def test_garbage_is_a_parse_failure_and_nothing_passes(tmp_path: Path) -> None:
    summary = _run(tmp_path, lambda user: "I would change money.py to handle negatives.")
    assert summary["pass_at_1"] == 0.0 and summary["parse_failures"] == 10
    assert summary["arm_valid"]


def test_a_forged_pass_is_scored_a_fail_and_listed(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    files = _solution("bk-01")
    files["tally/money.py"] += "\nimport os\n_EXIT = os._exit\n"
    summary = _run(tmp_path, lambda user: _blocks(files), cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["verdict"] == "pass"  # control: the verifier itself passed it
    assert record["passed"] is False and record["suspect"] == ["tally/money.py: os._exit"]
    assert summary["suspect"][0]["card_id"] == "bk-01"


def test_a_passing_control_invalidates_the_arm(tmp_path: Path) -> None:
    class AlwaysPass:
        overall_status = rb.VERDICT_PASS
        items = (type("I", (), {"item": {"description": "x"},
                                "handler_result": type("H", (), {"passed": True})()})(),)

    summary = _run(tmp_path, lambda user: _blocks({"notes.txt": "x"}),
                   verify=lambda **kw: AlwaysPass())
    assert summary["controls"]["passes"] == 2
    assert summary["arm_valid"] is False


def test_pass_pow_3_needs_every_run(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    good = _blocks(_solution("bk-01"))
    replies = iter([good, "nothing", good])
    summary = _run(tmp_path, lambda user: next(replies), cards=cards, runs=3)
    assert summary["pass_at_1"] == pytest.approx(2 / 3)
    assert summary["pass_pow_3"] == 0.0


def test_the_prompt_is_the_body_and_every_seed_file_never_the_frontmatter() -> None:
    card = rb.load_cards()[0]
    user = rb.user_message(card)
    assert user.startswith(card.body.rstrip("\n")[:40])
    assert "story_hash" not in user and "model_floor" not in user
    for path in rb._seed_files():
        assert f"<<<FILE {path.relative_to(rb.SEED).as_posix()}>>>" in user
    assert "bk-04" not in user and "control" not in user.lower()


def test_the_verifier_env_is_the_protocol_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0042")
    env = rb.verify_env()
    assert set(env) == {"PATH", "SystemRoot", "COMSPEC", "NoDefaultCurrentDirectoryInExePath"}
    assert env["NoDefaultCurrentDirectoryInExePath"] == "1"
    assert len(env["PATH"].split(";")) == 2


def test_the_frozen_tree_check(tmp_path: Path) -> None:
    assert rb.frozen_tree_problems() == []
    stray = rb.CARDS / "zz-unfrozen.md"
    stray.write_text("---\nid: zz\n---\n", encoding="utf-8")
    try:
        assert any("zz-unfrozen.md" in p for p in rb.frozen_tree_problems())
    finally:
        stray.unlink()
    assert rb.frozen_tree_problems() == []


def test_the_refusal_heuristic_both_ways() -> None:
    refuse = FakeClient(lambda u: "I'm sorry, but I can't help with that.")
    comply = FakeClient(lambda u: "Here is a script using socket.connect_ex ...")
    assert all(p["refused_heuristic"] for p in rb.refusal_probe(refuse))
    assert not any(p["refused_heuristic"] for p in rb.refusal_probe(comply))


def test_ollama_client_sends_the_native_payload_and_reads_timings() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body))
            reply = ({"message": {"content": "<<<FILE a.py>>>\nx\n<<<END FILE>>>",
                                  "thinking": "hmm"},
                      "prompt_eval_count": 900, "eval_count": 40, "eval_duration": 2e9}
                     if self.path == "/api/chat" else {"load_duration": 3e9})
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = rb.OllamaClient("m:1", host=f"http://127.0.0.1:{server.server_port}",
                                 num_ctx=16384)
        assert client.load()["ollama_load_seconds"] == 3.0
        reply = client.chat("SYS", "USER")
        client.unload()
    finally:
        server.shutdown()
    path, body = seen[1]
    assert path == "/api/chat" and body["stream"] is False
    assert body["options"]["num_ctx"] == 16384
    assert body["messages"] == [{"role": "system", "content": "SYS"},
                                {"role": "user", "content": "USER"}]
    assert reply.prompt_tokens == 900 and reply.output_tokens == 40
    assert reply.eval_seconds == 2.0 and reply.thinking_chars == 3
    assert seen[2][1]["keep_alive"] == 0  # unloaded


def test_a_model_error_is_recorded_not_raised(tmp_path: Path) -> None:
    client = rb.OllamaClient("m:1", host="http://127.0.0.1:9", timeout_sec=2)
    reply = client.chat("s", "u")
    assert reply.error and reply.content == ""


def test_a_reply_that_parses_but_changes_nothing_is_an_ac_failure(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    unchanged = (rb.SEED / "tally" / "money.py").read_text(encoding="utf-8")
    summary = _run(tmp_path, lambda user: _blocks({"tally/money.py": unchanged}), cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["parsed"] and not record["passed"] and record["verdict"] == "fail"
    assert summary["ac_failures"] == 1 and summary["parse_failures"] == 0
