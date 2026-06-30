"""Tests for `cards_runner.common.project_config`.

The project config is the chunk-5 per-project knob layer that sits
above DaemonConfig (host-wide). These tests cover:

- a missing project.yaml returns the empty default
- a valid project.yaml parses every knob
- malformed YAML degrades gracefully unless strict=True
- the hot-reload loader picks up an mtime bump
- reviewer / merge_gate sub-configs parse correctly
"""
from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest

from cards_runner.common.project_config import (
    ProjectConfig,
    ProjectConfigError,
    ProjectConfigLoader,
    load_project_config,
    resolve_project_config_path,
)


def test_default_is_all_none() -> None:
    cfg = ProjectConfig.default()
    assert cfg.story_source_path is None
    assert cfg.subjective_cascade_disabled is None
    assert cfg.sibling_reviewer.enabled is False
    assert cfg.amendment_reviewer.enabled is False
    assert cfg.merge_gate.auto_merge_tier_3_4 is False


def test_missing_file_returns_default(tmp_path: Path) -> None:
    cfg = load_project_config(tmp_path / "nope.yaml")
    assert cfg == ProjectConfig.default()


def test_missing_file_strict_raises(tmp_path: Path) -> None:
    with pytest.raises(ProjectConfigError):
        load_project_config(tmp_path / "nope.yaml", strict=True)


def test_full_yaml_parses(tmp_path: Path) -> None:
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            story_source_path: docs/story.md
            verifier:
              subjective_cascade_disabled: true
              skip_confidence_threshold: 0.92
            cascade:
              escalation_threshold: 0.55
              max_escalations: 1
            reviewers:
              sibling:
                enabled: true
                model: claude-sonnet-4-6
                label: my-reviewer
                cost_cap_usd: 0.75
                prompt_extra: "Focus on security."
              amendment:
                enabled: true
                model: claude-haiku-4-5-20251001
            merge_gate:
              auto_merge_tier_3_4: true
              pr_base_branch: develop
            """
        ).lstrip(),
        encoding="utf-8",
    )
    cfg = load_project_config(cfg_path)
    assert cfg.story_source_path == "docs/story.md"
    assert cfg.subjective_cascade_disabled is True
    assert cfg.verifier_skip_confidence_threshold == 0.92
    assert cfg.cascade_escalation_threshold == 0.55
    assert cfg.cascade_max_escalations == 1
    assert cfg.sibling_reviewer.enabled is True
    assert cfg.sibling_reviewer.model_id == "claude-sonnet-4-6"
    assert cfg.sibling_reviewer.label == "my-reviewer"
    assert cfg.sibling_reviewer.cost_cap_usd == 0.75
    assert cfg.sibling_reviewer.prompt_extra == "Focus on security."
    assert cfg.amendment_reviewer.enabled is True
    assert cfg.amendment_reviewer.model_id == "claude-haiku-4-5-20251001"
    assert cfg.merge_gate.auto_merge_tier_3_4 is True
    assert cfg.merge_gate.pr_base_branch == "develop"


def test_partial_yaml_uses_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("story_source_path: src/story.md\n", encoding="utf-8")
    cfg = load_project_config(cfg_path)
    assert cfg.story_source_path == "src/story.md"
    assert cfg.subjective_cascade_disabled is None
    assert cfg.sibling_reviewer.enabled is False


def test_malformed_yaml_returns_default(tmp_path: Path) -> None:
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(":::not yaml at all:::\n", encoding="utf-8")
    cfg = load_project_config(cfg_path)
    # Could parse to nothing or raise; non-strict mode degrades.
    assert cfg.story_source_path is None


def test_non_mapping_root_returns_default(tmp_path: Path) -> None:
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("- list_not_dict\n", encoding="utf-8")
    cfg = load_project_config(cfg_path)
    assert cfg.story_source_path is None


def test_loader_reload_on_mtime_bump(tmp_path: Path) -> None:
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("story_source_path: a.md\n", encoding="utf-8")
    loader = ProjectConfigLoader(cfg_path)
    assert loader.current().story_source_path == "a.md"
    # Bump the file's mtime explicitly (writing it again may produce the
    # same float on coarse filesystems).
    time.sleep(0.05)
    cfg_path.write_text("story_source_path: b.md\n", encoding="utf-8")
    new_mtime = cfg_path.stat().st_mtime + 1
    import os
    os.utime(cfg_path, (new_mtime, new_mtime))
    assert loader.reload_if_changed() is True
    assert loader.current().story_source_path == "b.md"


def test_loader_no_reload_when_unchanged(tmp_path: Path) -> None:
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("story_source_path: a.md\n", encoding="utf-8")
    loader = ProjectConfigLoader(cfg_path)
    assert loader.reload_if_changed() is False


def test_loader_path_none_is_inert() -> None:
    loader = ProjectConfigLoader(None)
    assert loader.current() == ProjectConfig.default()
    assert loader.reload_if_changed() is False


def test_resolve_explicit_path(tmp_path: Path) -> None:
    candidate = tmp_path / "elsewhere.yaml"
    assert resolve_project_config_path(candidate, todo_root=tmp_path) == candidate


def test_resolve_default_returns_none_when_absent(tmp_path: Path) -> None:
    assert resolve_project_config_path(None, todo_root=tmp_path) is None


def test_resolve_default_finds_todo_root_yaml(tmp_path: Path) -> None:
    (tmp_path / "project.yaml").write_text("verifier: {}\n", encoding="utf-8")
    resolved = resolve_project_config_path(None, todo_root=tmp_path)
    assert resolved is not None
    assert resolved.name == "project.yaml"
