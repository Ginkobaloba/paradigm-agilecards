"""Tests for `cards_runner.cli.doctor`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cards_runner.cli import doctor as doctor_mod
from cards_runner.cli.doctor import (
    build_report,
    render_json,
    render_text,
)
from cards_runner.common.types import DaemonConfig
from cards_runner.store.sqlite_store import SqliteRepository


def _cfg(tmp_path: Path, **overrides) -> DaemonConfig:
    base: dict = dict(
        todo_root=tmp_path,
        store_spec=f"sqlite:{tmp_path / 'cards.db'}",
    )
    base.update(overrides)
    return DaemonConfig(**base)


# ---- binary detection ------------------------------------------------


def test_binary_report_for_missing_returns_error(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, gh_path="this-binary-does-not-exist-zzzz")
    report = build_report(cfg, repo=None)
    gh_report = next(b for b in report.binaries if b.name == "gh")
    assert gh_report.resolved_path is None
    assert "not found" in gh_report.error


def test_binary_report_for_present_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub shutil.which so the test does not depend on what's on PATH.
    monkeypatch.setattr(
        doctor_mod, "shutil",
        type("S", (), {"which": staticmethod(lambda x: "/fake/path/" + x)})(),
    )
    # Stub _safe_version to skip the subprocess call.
    monkeypatch.setattr(
        doctor_mod, "_safe_version",
        lambda binary, args: ("fake version 1.0", ""),
    )
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None)
    git = next(b for b in report.binaries if b.name == "git")
    assert git.resolved_path == "/fake/path/git"
    assert git.version == "fake version 1.0"


def test_binary_report_includes_dolt_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor_mod, "shutil",
        type("S", (), {"which": staticmethod(lambda x: None)})(),
    )
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None, dolt_bin_env=r"C:\dolt\dolt.exe")
    dolt = next(b for b in report.binaries if b.name == "dolt")
    assert dolt.requested == r"C:\dolt\dolt.exe"


# ---- project config -------------------------------------------------


def test_project_config_missing_reports_missing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None)
    pc = report.project_config
    assert pc.source == "missing"
    assert pc.exists is False
    assert pc.sibling_reviewer_enabled is False


def test_project_config_explicit_path(tmp_path: Path) -> None:
    cfg_path = tmp_path / "custom.yaml"
    cfg_path.write_text(
        """
        reviewers:
          amendment:
            enabled: true
            auto_edit_ac: true
          sibling:
            enabled: true
            model: claude-sonnet-4-6
        merge_gate:
          auto_merge_tier_3_4: true
        story_source_path: docs/story.md
        """,
        encoding="utf-8",
    )
    cfg = _cfg(tmp_path, project_config_path=cfg_path)
    report = build_report(cfg, repo=None)
    pc = report.project_config
    assert pc.source == "explicit"
    assert pc.exists is True
    assert pc.sibling_reviewer_enabled is True
    assert pc.sibling_reviewer_model == "claude-sonnet-4-6"
    assert pc.amendment_reviewer_enabled is True
    assert pc.amendment_reviewer_auto_edit_ac is True
    assert pc.merge_gate_auto_merge_tier_3_4 is True
    assert pc.story_source_path == "docs/story.md"


def test_project_config_todo_root_default(tmp_path: Path) -> None:
    (tmp_path / "project.yaml").write_text(
        "reviewers:\n  sibling:\n    enabled: true\n",
        encoding="utf-8",
    )
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None)
    assert report.project_config.source == "todo_root_default"
    assert report.project_config.sibling_reviewer_enabled is True


# ---- schema -----------------------------------------------------------


def test_schema_section_skipped_when_no_repo(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None)
    assert report.schema == []
    assert any("schema section skipped" in n for n in report.notes)


def test_schema_section_reports_applied_when_repo_open(
    tmp_path: Path, store_path: Path,
) -> None:
    store = SqliteRepository.open(str(store_path))
    store.initialize_schema()
    try:
        cfg = _cfg(tmp_path, store_spec=f"sqlite:{store_path}")
        report = build_report(cfg, repo=store)
    finally:
        store.close()
    # pr_url is in ADDED_COLUMNS and initialize_schema applies it.
    pr_url_row = next(s for s in report.schema if s.column == "pr_url")
    assert pr_url_row.applied is True


# ---- knobs -----------------------------------------------------------


def test_knob_reports_mark_defaults_vs_overrides(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, pr_gate_enabled=True, sibling_reviewer_enabled=False)
    report = build_report(cfg, repo=None)
    pr_gate = next(k for k in report.knobs if k.name == "pr_gate_enabled")
    sibling = next(k for k in report.knobs if k.name == "sibling_reviewer_enabled")
    verifier = next(k for k in report.knobs if k.name == "verifier_enabled")
    assert pr_gate.value is True
    assert pr_gate.is_default is False
    assert sibling.is_default is True       # default is False; we set False.
    assert verifier.is_default is True      # default is True; cfg didn't override.


# ---- rendering -------------------------------------------------------


def test_render_text_contains_section_headers(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None)
    out = render_text(report)
    assert "todo_root:" in out
    assert "binaries:" in out
    assert "project config:" in out
    assert "schema migrations:" in out
    assert "knobs:" in out


def test_render_json_round_trips(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None)
    out = render_json(report)
    parsed = json.loads(out)
    assert parsed["todo_root"] == str(tmp_path)
    assert "binaries" in parsed
    assert "knobs" in parsed


def test_render_text_marks_overridden_knobs(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, pr_unblock_enabled=True)
    report = build_report(cfg, repo=None)
    out = render_text(report)
    assert "pr_unblock_enabled: True (overridden)" in out
