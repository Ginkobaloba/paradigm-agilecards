"""Tests for `cards_runner.daemon.ac_editor`."""
from __future__ import annotations

import textwrap

import pytest
import yaml

from cards_runner.daemon.ac_editor import (
    AcEditError,
    AmendmentEdit,
    splice_amendment,
)


def _body(items_yaml: str, *, prefix: str = "## Acceptance criteria\n\n") -> str:
    return prefix + "```yaml\n" + items_yaml.strip() + "\n```\n"


def _two_item_body() -> str:
    return _body(
        textwrap.dedent(
            """\
            acceptance_criteria:
              - description: "Smoke 1"
                type: file_exists
                path: README.md
              - description: "Smoke 2"
                type: file_contains
                path: README.md
                literal: hello
            """
        ),
    )


def _parsed_items(body: str) -> list[dict]:
    # Helper: re-parse the spliced body's AC list to inspect it.
    import re

    match = re.search(r"```ya?ml\s*\n(.*?)\n```", body, re.DOTALL | re.IGNORECASE)
    assert match is not None
    return yaml.safe_load(match.group(1))["acceptance_criteria"]


def test_splice_replaces_target_item_and_preserves_others() -> None:
    body = _two_item_body()
    edit = AmendmentEdit(
        ac_index=0,
        description="Smoke 1 (amended)",
        check_type="file_exists",
        check_fields={"path": "READMEv2.md"},
        amendment_reason="renamed README on disk",
        confidence=0.9,
        model_used="claude-sonnet-4-6",
    )
    out = splice_amendment(
        body, edit, reviewer_label="amend-reviewer-1",
        timestamp_iso="2026-05-22T12:00:00Z",
    )
    items = _parsed_items(out)
    assert len(items) == 2
    # Item 0 amended
    assert items[0]["description"] == "Smoke 1 (amended)"
    assert items[0]["type"] == "file_exists"
    assert items[0]["path"] == "READMEv2.md"
    assert items[0]["amended_at"] == "2026-05-22T12:00:00Z"
    assert items[0]["amended_by"] == "amend-reviewer-1"
    assert items[0]["amendment_reason"] == "renamed README on disk"
    # Original carries the pre-amendment item verbatim.
    assert items[0]["original"]["description"] == "Smoke 1"
    assert items[0]["original"]["type"] == "file_exists"
    assert items[0]["original"]["path"] == "README.md"
    # Item 1 untouched.
    assert items[1]["description"] == "Smoke 2"
    assert items[1]["type"] == "file_contains"
    assert items[1]["path"] == "README.md"
    assert items[1]["literal"] == "hello"
    assert "amended_at" not in items[1]


def test_splice_second_item_keeps_first_intact() -> None:
    body = _two_item_body()
    edit = AmendmentEdit(
        ac_index=1,
        description="Smoke 2 (amended)",
        check_type="file_contains",
        check_fields={"path": "README.md", "literal": "goodbye"},
        amendment_reason="copy changed",
        confidence=0.9,
    )
    out = splice_amendment(
        body, edit, reviewer_label="r", timestamp_iso="2026-05-22T12:00:00Z",
    )
    items = _parsed_items(out)
    assert items[0]["description"] == "Smoke 1"
    assert "amended_at" not in items[0]
    assert items[1]["description"] == "Smoke 2 (amended)"
    assert items[1]["literal"] == "goodbye"
    assert items[1]["original"]["literal"] == "hello"


def test_splice_out_of_range_index_raises() -> None:
    body = _two_item_body()
    edit = AmendmentEdit(
        ac_index=5,
        description="x", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    with pytest.raises(AcEditError, match="out of range"):
        splice_amendment(
            body, edit, reviewer_label="r",
            timestamp_iso="2026-05-22T12:00:00Z",
        )


def test_splice_negative_index_raises() -> None:
    body = _two_item_body()
    edit = AmendmentEdit(
        ac_index=-1,
        description="x", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    with pytest.raises(AcEditError):
        splice_amendment(
            body, edit, reviewer_label="r",
            timestamp_iso="2026-05-22T12:00:00Z",
        )


def test_splice_body_without_ac_block_raises() -> None:
    edit = AmendmentEdit(
        ac_index=0,
        description="x", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    with pytest.raises(AcEditError, match="no fenced YAML block"):
        splice_amendment(
            "just text, no fence",
            edit, reviewer_label="r",
            timestamp_iso="2026-05-22T12:00:00Z",
        )


def test_splice_body_with_non_list_ac_raises() -> None:
    body = _body("acceptance_criteria: not_a_list\n")
    edit = AmendmentEdit(
        ac_index=0,
        description="x", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    with pytest.raises(AcEditError, match="must be a YAML list"):
        splice_amendment(
            body, edit, reviewer_label="r",
            timestamp_iso="2026-05-22T12:00:00Z",
        )


def test_splice_body_with_malformed_yaml_raises() -> None:
    body = "```yaml\nthis: : : :\n```\n"
    edit = AmendmentEdit(
        ac_index=0,
        description="x", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    with pytest.raises(AcEditError, match="failed to parse"):
        splice_amendment(
            body, edit, reviewer_label="r",
            timestamp_iso="2026-05-22T12:00:00Z",
        )


def test_splice_empty_body_raises() -> None:
    edit = AmendmentEdit(
        ac_index=0, description="x", check_type="file_exists",
        amendment_reason="x", confidence=0.9,
    )
    with pytest.raises(AcEditError, match="empty"):
        splice_amendment(
            "", edit, reviewer_label="r",
            timestamp_iso="2026-05-22T12:00:00Z",
        )


def test_splice_legacy_acceptance_checks_alias_still_works() -> None:
    body = _body(
        textwrap.dedent(
            """\
            acceptance_checks:
              - description: "legacy"
                type: file_exists
                path: a.md
            """
        )
    )
    edit = AmendmentEdit(
        ac_index=0,
        description="new",
        check_type="file_exists",
        check_fields={"path": "b.md"},
        amendment_reason="moved",
        confidence=0.9,
    )
    out = splice_amendment(
        body, edit, reviewer_label="r",
        timestamp_iso="2026-05-22T12:00:00Z",
    )
    # The splice writes back under whichever key it found.
    import re

    match = re.search(r"```ya?ml\s*\n(.*?)\n```", out, re.DOTALL)
    assert match is not None
    loaded = yaml.safe_load(match.group(1))
    assert "acceptance_checks" in loaded
    assert loaded["acceptance_checks"][0]["path"] == "b.md"
    assert loaded["acceptance_checks"][0]["original"]["path"] == "a.md"


def test_splice_drops_provenance_keys_from_check_fields_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = _two_item_body()
    edit = AmendmentEdit(
        ac_index=0,
        description="amended",
        check_type="file_exists",
        check_fields={
            "path": "x.md",
            "amended_at": "1999-01-01T00:00:00Z",  # editor MAY NOT set this
            "amended_by": "evil-editor",            # nor this
        },
        amendment_reason="testing provenance guard",
        confidence=0.9,
    )
    with caplog.at_level("WARNING"):
        out = splice_amendment(
            body, edit, reviewer_label="legit-reviewer",
            timestamp_iso="2026-05-22T12:00:00Z",
        )
    items = _parsed_items(out)
    # Runner-owned provenance wins.
    assert items[0]["amended_at"] == "2026-05-22T12:00:00Z"
    assert items[0]["amended_by"] == "legit-reviewer"
    # The warning was emitted twice (once per dropped key).
    drop_warnings = [
        r for r in caplog.records
        if "tried to set provenance field" in r.message
    ]
    assert len(drop_warnings) == 2


def test_splice_with_indented_fence_preserves_indent() -> None:
    indented = (
        "Some prose.\n\n"
        "    ```yaml\n"
        "    acceptance_criteria:\n"
        "      - description: x\n"
        "        type: file_exists\n"
        "        path: a.md\n"
        "    ```\n"
    )
    edit = AmendmentEdit(
        ac_index=0, description="y", check_type="file_exists",
        check_fields={"path": "b.md"}, amendment_reason="x",
        confidence=0.9,
    )
    out = splice_amendment(
        indented, edit, reviewer_label="r",
        timestamp_iso="2026-05-22T12:00:00Z",
    )
    # The fence indent (4 spaces) is preserved on both fences.
    lines = out.splitlines()
    fence_lines = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("```")]
    for idx in fence_lines:
        assert lines[idx].startswith("    "), f"line {idx} dropped indent: {lines[idx]!r}"


def test_splice_preserves_surrounding_body() -> None:
    body = (
        "## Context\n\nThis card is important.\n\n"
        + _two_item_body()
        + "\n## Pointers\n\n- something.\n"
    )
    edit = AmendmentEdit(
        ac_index=0,
        description="Smoke 1 (v2)",
        check_type="file_exists",
        check_fields={"path": "v2.md"},
        amendment_reason="x",
        confidence=0.9,
    )
    out = splice_amendment(
        body, edit, reviewer_label="r",
        timestamp_iso="2026-05-22T12:00:00Z",
    )
    # Both prose sections still present.
    assert "## Context" in out
    assert "This card is important." in out
    assert "## Pointers" in out
    assert "- something." in out


def test_splice_original_is_deep_copy_not_alias() -> None:
    body = _body(
        textwrap.dedent(
            """\
            acceptance_criteria:
              - description: original
                type: command
                command: ["echo", "hi"]
                env:
                  KEY: value
            """
        )
    )
    edit = AmendmentEdit(
        ac_index=0,
        description="new",
        check_type="command",
        check_fields={"command": ["echo", "bye"]},
        amendment_reason="changed greeting",
        confidence=0.9,
    )
    out = splice_amendment(
        body, edit, reviewer_label="r",
        timestamp_iso="2026-05-22T12:00:00Z",
    )
    items = _parsed_items(out)
    assert items[0]["command"] == ["echo", "bye"]
    # Original captures the full pre-amendment item including nested env.
    assert items[0]["original"]["command"] == ["echo", "hi"]
    assert items[0]["original"]["env"] == {"KEY": "value"}


def test_splice_handles_item_with_no_extra_fields() -> None:
    body = _body(
        textwrap.dedent(
            """\
            acceptance_criteria:
              - description: tiny
                type: subjective
            """
        )
    )
    edit = AmendmentEdit(
        ac_index=0,
        description="tiny (clarified)",
        check_type="subjective",
        check_fields={},
        amendment_reason="rephrased the prompt",
        confidence=0.9,
    )
    out = splice_amendment(
        body, edit, reviewer_label="r",
        timestamp_iso="2026-05-22T12:00:00Z",
    )
    items = _parsed_items(out)
    assert items[0]["description"] == "tiny (clarified)"
    assert items[0]["original"]["description"] == "tiny"


def test_splice_strips_description_and_type_from_check_fields() -> None:
    """A model that helpfully duplicates description/type in check_fields
    shouldn't end up with duplicate keys in the spliced item."""
    body = _two_item_body()
    edit = AmendmentEdit(
        ac_index=0,
        description="canonical",
        check_type="file_exists",
        check_fields={
            "description": "redundant",   # should be dropped
            "type": "file_exists",         # should be dropped
            "path": "real.md",
        },
        amendment_reason="x",
        confidence=0.9,
    )
    out = splice_amendment(
        body, edit, reviewer_label="r",
        timestamp_iso="2026-05-22T12:00:00Z",
    )
    items = _parsed_items(out)
    assert items[0]["description"] == "canonical"
    assert items[0]["type"] == "file_exists"
    assert items[0]["path"] == "real.md"
