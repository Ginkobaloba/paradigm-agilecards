"""Extract the acceptance_criteria YAML block from a card body.

RUNNER_CONTRACT.md "Acceptance check execution": the runner parses
the fenced `acceptance_criteria:` YAML block under the card body's
"## Acceptance criteria" section, then dispatches each item through
`verifier.runner.verify_card`. The legacy single-block name
`acceptance_checks:` is retained as a deprecated alias.

This module owns only the parse-and-normalize step. Dispatch lives in
`verifier.runner`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import yaml  # type: ignore[import-untyped]

from .types import SchemaError, canonicalize_type


log = logging.getLogger(__name__)


# A fenced code block whose info line starts with `yaml`. We accept
# `yaml` (canonical) and `yml` (defensive); the language-tag is
# case-insensitive in CommonMark, which is how the planner emits it.
_YAML_FENCE_RE = re.compile(
    r"```ya?ml\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class AcceptanceItem:
    """One normalized acceptance-criterion item.

    `type` is the canonical name (legacy aliases already mapped).
    `raw` is the item dict as the card declared it -- the verifier's
    handlers read fields off `raw`, so a typo there fails at dispatch
    rather than being silently dropped at normalize time.
    """

    index: int
    type: str
    raw: dict[str, Any]
    used_alias: bool = False

    @property
    def description(self) -> str:
        desc = self.raw.get("description")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()
        return f"AC#{self.index} ({self.type})"

    @property
    def subjective(self) -> bool:
        return self.type == "subjective"


def extract_yaml_block(body_md: str) -> str | None:
    """Return the YAML text of the first ```yaml``` fence in the body.

    Returns None when the body carries no YAML fence. Multiple fences
    are not supported: the first one wins, which matches the planner's
    single-block convention.
    """
    if not body_md:
        return None
    match = _YAML_FENCE_RE.search(body_md)
    if match is None:
        return None
    return match.group("body")


def parse_acceptance_block(body_md: str) -> list[AcceptanceItem]:
    """Parse the body's acceptance-criteria YAML block.

    Returns an empty list when the body carries no YAML block or
    declares no items. Raises `SchemaError` if the block is present but
    not a mapping with an `acceptance_criteria` (or legacy
    `acceptance_checks`) list-typed key, or any item declares an
    unknown `type:`.
    """
    yaml_text = extract_yaml_block(body_md)
    if yaml_text is None:
        return []
    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise SchemaError(f"acceptance_criteria YAML failed to parse: {exc}") from exc
    if loaded in (None, ""):
        return []
    if not isinstance(loaded, dict):
        raise SchemaError(
            "acceptance_criteria block must be a YAML mapping; got "
            f"{type(loaded).__name__}"
        )
    items_raw = loaded.get("acceptance_criteria")
    if items_raw is None:
        # Deprecated v1.2 alias. The contract keeps it through v1.3.
        items_raw = loaded.get("acceptance_checks")
        if items_raw is not None:
            log.warning(
                "card uses deprecated `acceptance_checks:` key; "
                "rename to `acceptance_criteria:` (v1.3)"
            )
    if items_raw is None:
        return []
    if not isinstance(items_raw, list):
        raise SchemaError(
            "`acceptance_criteria` must be a YAML list; got "
            f"{type(items_raw).__name__}"
        )

    out: list[AcceptanceItem] = []
    for i, raw in enumerate(items_raw):
        if not isinstance(raw, dict):
            raise SchemaError(
                f"AC item #{i} must be a YAML mapping; got "
                f"{type(raw).__name__}"
            )
        # The v1.2 form had `subjective: true` rather than `type:`.
        # Preserve the alias for through-v1.3 compatibility (contract:
        # "the v1.2 flag is preserved as a deprecated alias through
        # v1.3").
        declared_type = raw.get("type")
        if declared_type is None and raw.get("subjective") is True:
            log.warning(
                "AC item #%d uses deprecated `subjective: true` flag; "
                "migrate to `type: subjective`", i
            )
            declared_type = "subjective"
        if declared_type is None:
            raise SchemaError(f"AC item #{i} is missing `type:`")
        canonical, used_alias = canonicalize_type(str(declared_type))
        if used_alias:
            log.warning(
                "AC item #%d uses deprecated type alias %r -> %r",
                i, declared_type, canonical,
            )
        out.append(
            AcceptanceItem(index=i, type=canonical, raw=raw, used_alias=used_alias)
        )
    return out
