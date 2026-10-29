"""The scorer, with fake models. No GPU, no Ollama.

The first test is the scorer's own positive control: a model that answers
every card with its reference solution must pass all ten exam cards and fail
both negative controls, through the real verifier and real pytest.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
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


def test_a_suspect_token_is_flagged_for_a_human_and_does_not_change_the_score(
        tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    files = _solution("bk-01")
    files["tally/money.py"] += "\nimport os\n_EXIT = os._exit\n"
    summary = _run(tmp_path, lambda user: _blocks(files), cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["verdict"] == "pass" and record["suspect"] == ["tally/money.py: os._exit"]
    assert record["passed"] is True and record["forged"] is False  # the canary failed as it must
    assert summary["suspect"][0]["card_id"] == "bk-01" and summary["pass_at_1"] == 1.0


_SEED_INIT = (rb.SEED / "tally" / "__init__.py").read_text(encoding="utf-8")
# The review's two forgery shapes (S-F2, I-F2): neither uses a listed token.
_FD_FORGE = ("\nfrom os import _exit as _x, write as _w\nfor _fd in range(64):\n"
             "    try:\n        _w(_fd, b'8 passed in 0.01s\\n')\n    except OSError:\n"
             "        pass\n_x(0)\n")
_RUNTEST_PATCH = "\n_m = __import__('py' + 'test')\n_m.Function.runtest = lambda self: None\n"


@pytest.mark.parametrize("forge", [_FD_FORGE, _RUNTEST_PATCH], ids=["fd-write", "runtest-patch"])
def test_a_forgery_the_tokens_miss_is_caught_by_the_canary(tmp_path: Path, forge: str) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    # The seed money.py, unfixed: only the forgery can make bk-01 pass.
    files = {"tally/__init__.py": _SEED_INIT + forge}
    summary = _run(tmp_path, lambda user: _blocks(files), cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["verdict"] == "pass" and record["suspect"] == []  # the verifier was fooled
    assert record["forged"] is True and record["passed"] is False
    assert summary["arm_valid"] is False and summary["forged"] == [{"card_id": "bk-01", "run": 1}]


@pytest.mark.parametrize("canary,forged", [
    ({"exit_code": 1, "output": "F\n1 failed in 0.20s"}, False),
    ({"exit_code": 0, "output": "1 passed in 0.01s"}, True),
    ({"exit_code": 1, "output": "1 failed, 1 passed in 0.2s"}, True),
    ({"exit_code": 1, "output": "2 failed in 0.2s"}, True),
    ({"exit_code": 1, "output": "ERROR: file not found"}, True),
    ({"exit_code": 0, "output": "1 failed in 0.20s"}, True),
    (None, False),
])
def test_canary_verdicts(canary: dict[str, Any] | None, forged: bool) -> None:
    assert rb.canary_forged(canary) is forged


def test_a_looping_answer_is_killed_with_its_whole_process_tree(tmp_path: Path) -> None:
    """S-F1: the shell handler's timeout killed only cmd.exe and then waited
    forever on pytest. The job kills the tree at the wall-clock limit."""
    card = next(c for c in rb.load_cards() if c.card_id == "bk-01")
    pids = tmp_path / "pids.txt"
    loop = (f"\nimport os\nwith open({str(pids)!r}, 'a') as _f:\n"
            f"    _f.write(str(os.getpid()) + '\\n')\nwhile True:\n    pass\n")
    started = time.time()
    record = rb.score_reply(card, _blocks({"tally/__init__.py": _SEED_INIT + loop}), wall_sec=15)
    elapsed = time.time() - started
    assert record["verifier_timeout"] is True and record["passed"] is False
    assert elapsed < 45, elapsed
    hung = [int(p) for p in pids.read_text().split()]
    assert hung, "the looping pytest never started, so this proved nothing"
    for pid in hung:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                             text=True).stdout
        assert str(pid) not in out, f"pytest {pid} outlived the job"


def test_ac_evidence_is_kept(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    _run(tmp_path, lambda user: _blocks({"tally/money.py": "x = 1\n"}), cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["ac"] and all("evidence" in item for item in record["ac"])
    assert any(item["evidence"] for item in record["ac"])


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


def test_the_pinned_digest_is_the_exam_on_disk() -> None:
    assert rb.frozen_tree_problems() == []
    assert len(rb.exam_files()) == 55 and "canary/test_canary.py" in rb.exam_files()
    assert not any(p.startswith("harness/") for p in rb.exam_files())


@pytest.fixture
def exam_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A byte copy of the exam, outside git, that main() is pointed at."""
    bench = tmp_path / "bench"
    shutil.copytree(rb.BENCH, bench, ignore=shutil.ignore_patterns("harness"))
    monkeypatch.setattr(rb, "BENCH", bench)
    monkeypatch.setattr(rb, "scorer_problems", lambda: [])
    monkeypatch.setattr(rb, "results_problems", lambda: [])
    return bench


def _crlf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


TREE_EDITS = {
    "edit a tracked card": lambda b: (b / "cards/bk-01.md").write_text(
        (b / "cards/bk-01.md").read_text(encoding="utf-8") + "\nHint: negate first.\n",
        encoding="utf-8"),
    "delete a card": lambda b: (b / "cards/bk-12.md").unlink(),
    "delete a control": lambda b: (b / "cards/bk-04.md").unlink(),
    "CRLF-rewrite PROTOCOL.md": lambda b: _crlf(b / "PROTOCOL.md"),
    "edit the system prompt": lambda b: (b / "SYSTEM_PROMPT.txt").write_text("Be brief.\n"),
    "edit a seed file": lambda b: (b / "seed_repo/tally/money.py").write_text("x = 1\n"),
    "edit a reference solution": lambda b: (b / "solutions/bk-01/tally/money.py").write_text("\n"),
    "edit the canary": lambda b: (b / "canary/test_canary.py").write_text("def test_x(): pass\n"),
    "add an untracked card": lambda b: (b / "cards/zz.md").write_text("---\nid: zz\n---\n"),
    "plant a cache file in the seed": lambda b: (
        (b / "seed_repo/tally/__pycache__").mkdir(),
        (b / "seed_repo/tally/__pycache__/money.cpython-311.pyc").write_bytes(b"\0")),
    "rename a card": lambda b: (b / "cards/bk-07.md").rename(b / "cards/bk-07x.md"),
}


@pytest.mark.parametrize("edit", TREE_EDITS, ids=list(TREE_EDITS))
def test_main_refuses_any_change_to_the_exam(edit: str, exam_copy: Path,
                                            monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    assert rb.exam_digest(exam_copy) == rb.EXAM_DIGEST  # control: the copy is the exam
    TREE_EDITS[edit](exam_copy)
    monkeypatch.setattr(rb, "run_arm", lambda **kw: pytest.fail("reached run_arm"))
    assert rb.main(["--arm", "A", "--no-lease"]) == 2
    assert "the exam is not the frozen one" in capsys.readouterr().out


def test_main_reaches_the_arm_on_the_unedited_copy(exam_copy: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control for the refusals above."""
    reached = []
    monkeypatch.setattr(rb, "positive_control", lambda: [])  # tested on its own below
    monkeypatch.setattr(rb, "run_arm", lambda **kw: reached.append(kw) or {"arm_valid": True})
    monkeypatch.setattr(rb, "render_md", lambda s: "")
    assert rb.main(["--arm", "A", "--no-lease"]) == 0
    assert reached and reached[0]["arm"] == "A"


def test_harness_and_runner_edits_refuse_and_ignored_results_do_not() -> None:
    assert not any("zz_planted" in p for p in rb.scorer_problems())
    planted = rb.HERE / "zz_planted.py"
    ignored = rb.HERE / "results" / "zz_planted.txt"
    ignored.parent.mkdir(exist_ok=True)
    planted.write_text("x = 1\n")
    ignored.write_text("x\n")
    try:
        problems = rb.scorer_problems()
        assert any("zz_planted.py" in p for p in problems)
        assert not any("zz_planted.txt" in p for p in problems)
    finally:
        planted.unlink()
        ignored.unlink()


def test_results_from_another_exam_refuse(tmp_path: Path) -> None:
    (tmp_path / "A-1").mkdir()
    (tmp_path / "A-1" / "runs.jsonl").write_text(json.dumps({"exam_digest": rb.EXAM_DIGEST}) + "\n")
    assert rb.results_problems(tmp_path) == []
    (tmp_path / "A-0").mkdir()
    (tmp_path / "A-0" / "runs.jsonl").write_text(json.dumps({"exam_freeze": "f814f9c"}) + "\n")
    assert rb.results_problems(tmp_path) == ["A-0: exam_digest None"]


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
                                  "thinking": "hmm"}, "done": True, "done_reason": "stop",
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


# ---- PROTOCOL.md amendment A1 and labeling ------------------------------


@pytest.mark.parametrize("content,expected,flag", [
    ("<think>plan</think>answer", "answer", True),
    ("a<think>x\ny</think>b<think>z</think>c", "abc", True),
    ("<think>never closed\n<<<FILE a.py>>>", "<think>never closed\n<<<FILE a.py>>>", False),
    ("<THINK>upper</THINK>kept", "<THINK>upper</THINK>kept", False),
    ("no reasoning", "no reasoning", False),
])
def test_strip_inline_think(content: str, expected: str, flag: bool) -> None:
    assert rb.strip_inline_think(content) == (expected, flag)


def test_a_block_only_inside_think_fails_primary_and_passes_raw(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    reply = "<think>\n" + _blocks(_solution("bk-01")) + "</think>\nDone."
    summary = _run(tmp_path, lambda user: reply, cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["inline_think"] is True
    assert record["passed"] is False and record["parse_failure"] == "no complete block"
    assert record["raw_passed"] is True  # the sensitivity line sees the difference
    assert summary["pass_at_1"] == 0.0 and summary["pass_at_1_raw"] == 1.0
    assert (tmp_path / "out" / "replies" / "bk-01-r1.stripped.txt").is_file()


def test_a_decoy_in_think_does_not_count_against_the_real_answer(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    decoy = _blocks({"tally/money.py": "broken = True"})
    reply = f"<think>\n{decoy}</think>\n" + _blocks(_solution("bk-01"))
    summary = _run(tmp_path, lambda user: reply, cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["passed"] is True  # primary: the decoy is gone
    assert record["raw_passed"] is False  # raw: a duplicate path is a parse failure
    assert summary["think_flags"]["inline_think"] == 1


# ---- PROTOCOL.md v2: A2, outcomes, the infra rule, validity ---------------


@pytest.mark.parametrize("content,expected,flags", [
    ("draft</think>answer", "answer", {"orphan_think_close"}),
    ("x<think>a</think>y", "xy", {"inline_think"}),
    ("pre</think>a<think>b</think>c", "ac", {"orphan_think_close", "inline_think"}),
    ("<think>never closed", "<think>never closed", {"unclosed_think"}),
    ("<think>a<think>b</think>c</think>d", "c</think>d", {"inline_think", "nested_think",
                                                          "unclosed_think"}),
    ("plain", "plain", set()),
])
def test_strip_think_a2_then_a1(content: str, expected: str, flags: set[str]) -> None:
    stripped, got = rb.strip_think(content)
    assert stripped == expected
    assert {k for k, v in got.items() if v} == flags


def test_an_orphan_closer_drops_the_draft_before_it(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    reply = (_blocks({"tally/money.py": "broken = True"}) + "</think>\n"
             + _blocks(_solution("bk-01")))
    summary = _run(tmp_path, lambda user: reply, cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["orphan_think_close"] is True and record["passed"] is True
    assert record["raw_passed"] is False  # the sensitivity line sees A2 too
    assert summary["think_flags"]["orphan_think_close"] == 1


def _row(cid: str, out: str, run: int = 1, **kw: Any) -> dict[str, Any]:
    return {"card_id": cid, "run": run, "outcome": out, "passed": out == "pass",
            "forged": out == "forged", "touched_tests": [], "suspect": [],
            "started_at": 0.0, "ended_at": 1.0, **kw}


EXAM_IDS = sorted({f"bk-{i:02d}" for i in range(1, 13)} - rb.NEGATIVE_CONTROLS)


def _arm(outcomes_by_run: list[dict[str, str]], runs: int) -> list[dict[str, Any]]:
    rows = []
    for run in range(1, runs + 1):
        for cid in EXAM_IDS:
            rows.append(_row(cid, outcomes_by_run[run - 1].get(cid, "pass"), run))
        rows += [_row(cid, "ac_failure", run, verdict="fail") for cid in sorted(rb.NEGATIVE_CONTROLS)]
    return rows


def test_infra_errors_leave_the_denominator_and_are_reported() -> None:
    rows = _arm([{"bk-01": "infra_error"}, {}, {}], runs=3)
    s = rb.summarize("T", "m", rows, runs=3)
    assert s["card_runs"] == 30 and s["answered_card_runs"] == 29 and s["infra_errors"] == 1
    assert s["pass_at_1"] == 1.0 and s["pass_at_1_errors_as_fails"] == pytest.approx(29 / 30)
    assert s["pass_pow_3"] == 1.0 and s["pass_pow_3_cards_left_out"] == 1
    assert s["arm_valid"], s["invalid_reasons"]


def test_the_infra_cap_is_ten_percent_of_exam_card_runs() -> None:
    three = _arm([{"bk-01": "infra_error"}, {"bk-02": "infra_error"}, {"bk-03": "infra_error"}], 3)
    assert rb.summarize("T", "m", three, runs=3)["arm_valid"]  # 3/30 is not more than 10%
    four = _arm([{"bk-01": "infra_error", "bk-05": "infra_error"}, {"bk-02": "infra_error"},
                 {"bk-03": "infra_error"}], 3)
    s = rb.summarize("T", "m", four, runs=3)
    assert not s["arm_valid"] and "exceed 10%" in s["invalid_reasons"][0]


def test_a_card_with_no_answered_run_invalidates_the_arm() -> None:
    rows = _arm([{"bk-07": "infra_error"}] * 3, runs=3)  # 3/30: under the cap
    s = rb.summarize("T", "m", rows, runs=3)
    assert not s["arm_valid"] and s["invalid_reasons"] == ["bk-07 has no non-error run"]


def test_a_control_counts_as_passed_by_its_verdict_even_when_flagged() -> None:
    """S-F3: v1 counted control passes from `passed`, which a flag could mask."""
    rows = _arm([{}], runs=1)
    control = next(r for r in rows if r["card_id"] == "bk-04")
    control.update(verdict=rb.VERDICT_PASS, suspect=["tally/legacy.py: sys.modules"])
    s = rb.summarize("T", "m", rows, runs=1)
    assert not s["arm_valid"] and s["controls"]["passes"] == 1


@pytest.mark.parametrize("drop", ["bk-04", "bk-09"])
def test_controls_that_did_not_run_invalidate_the_arm(drop: str) -> None:
    rows = [r for r in _arm([{}], runs=1) if r["card_id"] != drop]
    s = rb.summarize("T", "m", rows, runs=1)
    assert not s["arm_valid"] and "controls ran 1 times, not 2" in s["invalid_reasons"]


def test_time_statistics_cover_answered_exam_runs_only() -> None:
    rows = _arm([{"bk-01": "infra_error"}], runs=1)
    answered = [r for r in rows if r["outcome"] != "infra_error" and r["card_id"] in EXAM_IDS]
    for seconds, r in enumerate(answered, start=1):  # 1..9: the median is 5
        r["ended_at"] = float(seconds)
    for r in rows:
        if r not in answered:  # the infra row and both controls
            r["ended_at"] = 0.001 if r["outcome"] == "infra_error" else 100.0
    assert rb.summarize("T", "m", rows, runs=1)["median_card_seconds"] == 5.0


@pytest.mark.parametrize("reply_kw,record_kw,expected", [
    ({"error_kind": "infra"}, {}, "infra_error"),
    ({"error_kind": "timeout"}, {}, "model_timeout"),
    ({"done_reason": "length"}, {"parsed": False}, "truncated"),
    ({"prompt_tokens": 5600, "output_tokens": 26600}, {"parsed": False}, "truncated"),
    ({"prompt_tokens": 5600, "output_tokens": 900}, {"parsed": False}, "parse_failure"),
    ({}, {"parsed": True}, "ac_failure"),
    ({}, {"parsed": True, "verifier_timeout": True}, "verifier_timeout"),
    ({}, {"parsed": True, "forged": True}, "forged"),
    ({"done_reason": "length"}, {"parsed": True, "passed": True}, "pass"),
])
def test_outcomes(reply_kw: dict[str, Any], record_kw: dict[str, Any], expected: str) -> None:
    record = {"passed": False, "forged": False, "verifier_timeout": False, "parsed": False,
              **record_kw}
    assert rb.outcome(record, rb.Reply("", 0.0, 1.0, **reply_kw), 32768) == expected


def _server(handler_fn):  # noqa: ANN001, ANN202
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            status, body = handler_fn(self.path)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("status,body,kind", [
    (500, {"error": "boom"}, "infra"),
    (200, {"error": "model failed to load"}, "infra"),
    (200, {"message": {"content": "x"}, "done": False}, "infra"),
    (200, {"message": {"content": "x"}}, "infra"),
    (200, {"message": {"content": "x"}, "done": True, "done_reason": "stop"}, None),
])
def test_the_client_tells_an_infra_error_from_an_answer(status: int, body: dict[str, Any],
                                                         kind: str | None) -> None:
    server = _server(lambda path: (status, body))
    try:
        reply = rb.OllamaClient("m:1", host=f"http://127.0.0.1:{server.server_port}").chat("s", "u")
    finally:
        server.shutdown()
    assert reply.error_kind == kind
    assert (reply.content == "") == (kind is not None)


def test_a_slow_model_is_a_model_timeout_not_infra() -> None:
    def slow(path: str) -> tuple[int, dict[str, Any]]:
        time.sleep(3)
        return 200, {"message": {"content": "late"}, "done": True}

    server = _server(slow)
    try:
        reply = rb.OllamaClient("m:1", host=f"http://127.0.0.1:{server.server_port}",
                                timeout_sec=0.5).chat("s", "u")
    finally:
        server.shutdown()
    assert reply.error_kind == "timeout"


def test_an_infra_error_is_recorded_unscored_through_run_arm(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]

    class Down(FakeClient):
        def chat(self, system: str, user: str) -> rb.Reply:
            return rb.Reply("", 0.0, 0.0, error="URLError: refused", error_kind="infra")

    summary = rb.run_arm(arm="T", client=Down(lambda u: ""), runs=1, out_dir=tmp_path / "out",
                         vram=NoVram(), probe=False, cards=cards)
    record = json.loads((tmp_path / "out" / "runs.jsonl").read_text().splitlines()[0])
    assert record["outcome"] == "infra_error" and record["parsed"] is False
    assert record["parse_failure"] is None and "verdict" not in record  # never scored
    assert summary["outcomes"]["parse_failure"] == 0 and summary["infra_errors"] == 1
    assert summary["pass_at_1"] is None and not summary["arm_valid"]


def test_the_positive_control_passes_on_the_real_toolchain() -> None:
    assert rb.positive_control() == []


def test_the_positive_control_catches_a_venv_without_python(monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]
                                                            ) -> None:
    """S-F5/I-F4: a dead toolchain scored as a weak model in v1."""
    real = rb.verify_env

    def broken() -> dict[str, str]:
        env = real()
        env["PATH"] = env["PATH"].split(";")[-1]  # System32 only: no python
        return env

    monkeypatch.setattr(rb, "verify_env", broken)
    problems = rb.positive_control()
    # All ten: the eight pytest cards fail their AC, and the two file-only
    # cards (bk-05, bk-08) pass their ACs but the canary cannot run.
    assert len(problems) == 10 and all("reference failed" in p for p in problems)
    assert sum("canary" in p for p in problems) == 2
    for name in ("scorer_problems", "frozen_tree_problems", "results_problems"):
        monkeypatch.setattr(rb, name, lambda: [])
    monkeypatch.setattr(rb, "run_arm", lambda **kw: pytest.fail("reached run_arm"))
    assert rb.main(["--arm", "A", "--no-lease"]) == 2
    assert "toolchain is broken" in capsys.readouterr().out


def test_every_row_and_the_summary_carry_the_exam_and_the_scorer(tmp_path: Path) -> None:
    cards = [c for c in rb.load_cards() if c.card_id == "bk-01"]
    summary = _run(tmp_path, lambda user: "nothing", cards=cards)
    head = rb._git("rev-parse", "HEAD").strip()
    scorer = {name: hashlib.sha256((rb.HERE / name).read_bytes()).hexdigest()
              for name in ("run_bakeoff.py", "reply_parser.py", "verify_child.py")}
    for item in [json.loads(line) for line in
                 (tmp_path / "out" / "runs.jsonl").read_text().splitlines()] + [summary]:
        assert item["exam_digest"] == rb.EXAM_DIGEST and item["exam_version"] == "v2"
        assert item["repo_head"] == head and item["scorer"] == scorer
        assert item["pytest"] and item["python"]
    md = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert rb.EXAM_DIGEST in md and head in md


def test_an_arm_is_pinned_to_its_tag(capsys: pytest.CaptureFixture[str]) -> None:
    assert rb.ARMS["B"] == "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL"
    rc = rb.main(["--arm", "B", "--model", "qwen3.8:27b", "--no-lease"])
    assert rc == 2 and "pinned" in capsys.readouterr().out
