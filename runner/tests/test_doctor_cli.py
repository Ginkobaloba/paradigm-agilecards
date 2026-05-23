"""End-to-end test for `cards-runner doctor` via the CLI entrypoint."""
from __future__ import annotations

import json
from pathlib import Path

from cards_runner.cli.__main__ import main


def test_doctor_runs_with_skip_store_and_emits_json(
    tmp_path: Path, capsys,
) -> None:
    rc = main([
        "doctor",
        "--todo-root", str(tmp_path),
        "--skip-store",
        "--json",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["todo_root"] == str(tmp_path)
    assert "binaries" in parsed
    assert "knobs" in parsed
    # With --skip-store, schema is empty + a note explains why.
    assert parsed["schema"] == []
    assert any("schema section skipped" in n for n in parsed["notes"])


def test_doctor_runs_with_store_introspection(
    tmp_path: Path, capsys,
) -> None:
    rc = main([
        "doctor",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{tmp_path / 'cards.db'}",
        "--json",
    ])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    # The store was opened + initialized, so pr_url is applied.
    pr_url = next(s for s in parsed["schema"] if s["column"] == "pr_url")
    assert pr_url["applied"] is True


def test_doctor_text_output_includes_section_headers(
    tmp_path: Path, capsys,
) -> None:
    rc = main([
        "doctor",
        "--todo-root", str(tmp_path),
        "--skip-store",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "binaries:" in out
    assert "project config:" in out
    assert "schema migrations:" in out
    assert "knobs:" in out
