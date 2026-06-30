"""Acceptance-criteria YAML block parsing.

Token-free. Exercises the YAML-fence extractor, the canonical-type
registry, and the legacy alias table.
"""
from __future__ import annotations

import pytest

from cards_runner.verifier.parse import (
    AcceptanceItem,
    extract_yaml_block,
    parse_acceptance_block,
)
from cards_runner.verifier.types import SchemaError, canonicalize_type


def test_canonicalize_known_type_round_trips() -> None:
    name, alias = canonicalize_type("file_exists")
    assert name == "file_exists"
    assert alias is False


def test_canonicalize_legacy_alias() -> None:
    name, alias = canonicalize_type("grep_match")
    assert name == "file_contains"
    assert alias is True


def test_canonicalize_unknown_raises() -> None:
    with pytest.raises(SchemaError):
        canonicalize_type("teleport")


def test_canonicalize_empty_raises() -> None:
    with pytest.raises(SchemaError):
        canonicalize_type("")


def test_extract_yaml_block_picks_first_fence() -> None:
    body = "## A\n\n```yaml\nfoo: 1\n```\n\nmore text\n```yaml\nbar: 2\n```"
    block = extract_yaml_block(body)
    assert block is not None
    assert "foo: 1" in block
    assert "bar: 2" not in block


def test_extract_yaml_block_absent() -> None:
    assert extract_yaml_block("## A\n\nno code fences here") is None


def test_parse_empty_returns_empty_list() -> None:
    assert parse_acceptance_block("") == []


def test_parse_canonical_block() -> None:
    body = (
        "## Acceptance criteria\n\n"
        "```yaml\n"
        "acceptance_criteria:\n"
        "  - description: 'file exists'\n"
        "    type: file_exists\n"
        "    path: README.md\n"
        "  - description: 'tests pass'\n"
        "    type: shell\n"
        "    command: pytest -q\n"
        "```\n"
    )
    items = parse_acceptance_block(body)
    assert len(items) == 2
    assert items[0].type == "file_exists"
    assert items[0].index == 0
    assert items[0].raw["path"] == "README.md"
    assert items[1].type == "shell"
    assert items[1].raw["command"] == "pytest -q"


def test_parse_legacy_acceptance_checks_key() -> None:
    body = (
        "```yaml\n"
        "acceptance_checks:\n"
        "  - description: 'old key'\n"
        "    type: file_exists\n"
        "    path: README.md\n"
        "```\n"
    )
    items = parse_acceptance_block(body)
    assert len(items) == 1
    assert items[0].type == "file_exists"


def test_parse_legacy_subjective_flag_normalizes() -> None:
    body = (
        "```yaml\n"
        "acceptance_criteria:\n"
        "  - description: 'opinion'\n"
        "    subjective: true\n"
        "```\n"
    )
    items = parse_acceptance_block(body)
    assert items[0].type == "subjective"
    assert items[0].subjective is True


def test_parse_legacy_type_alias_used_alias_flag() -> None:
    body = (
        "```yaml\n"
        "acceptance_criteria:\n"
        "  - description: 'old name'\n"
        "    type: grep_match\n"
        "    path: x\n"
        "    pattern: y\n"
        "```\n"
    )
    items = parse_acceptance_block(body)
    assert items[0].type == "file_contains"
    assert items[0].used_alias is True


def test_parse_unknown_type_raises() -> None:
    body = (
        "```yaml\n"
        "acceptance_criteria:\n"
        "  - description: 'bogus'\n"
        "    type: teleport\n"
        "```\n"
    )
    with pytest.raises(SchemaError):
        parse_acceptance_block(body)


def test_parse_non_mapping_block_raises() -> None:
    body = "```yaml\n- just a list\n```\n"
    with pytest.raises(SchemaError, match="must be a YAML mapping"):
        parse_acceptance_block(body)


def test_parse_non_list_items_raises() -> None:
    body = "```yaml\nacceptance_criteria: not a list\n```\n"
    with pytest.raises(SchemaError, match="must be a YAML list"):
        parse_acceptance_block(body)


def test_acceptance_item_description_property() -> None:
    item = AcceptanceItem(
        index=2,
        type="file_exists",
        raw={"description": "  needs README  "},
    )
    assert item.description == "needs README"


def test_acceptance_item_description_fallback() -> None:
    item = AcceptanceItem(index=3, type="shell", raw={})
    assert item.description == "AC#3 (shell)"
