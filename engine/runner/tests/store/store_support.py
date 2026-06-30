"""Helper functions for the storage-layer test suite.

Fixtures live in `conftest.py`; this module holds only plain helpers
so the test files can import it without tripping pytest's fixture
discovery.
"""
from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path
from typing import Any


def dolt_available() -> bool:
    """True when a usable `dolt` binary is reachable."""
    if os.environ.get("CARDS_DOLT_BIN"):
        return Path(os.environ["CARDS_DOLT_BIN"]).is_file()
    return shutil.which("dolt") is not None


def make_card_text(
    card_id: str,
    *,
    status: str = "backlog",
    points: int = 2,
    depends_on: list[str] | None = None,
    cascade_history: list[dict[str, Any]] | None = None,
    body: str | None = None,
) -> str:
    """Build a well-formed v1 card `.md` text.

    The frontmatter and body are assembled with exact `---` fences and
    the blank line after the closing fence that real cards carry, so
    the result exercises the verbatim round trip honestly.
    """
    deps = depends_on or []
    deps_block = (
        "depends_on: []"
        if not deps
        else "depends_on:\n" + "\n".join(f"  - {d}" for d in deps)
    )
    casc = cascade_history or []
    if not casc:
        casc_block = "cascade_history: []"
    else:
        lines = ["cascade_history:"]
        for entry in casc:
            first = True
            for key, value in entry.items():
                prefix = "  - " if first else "    "
                lines.append(f"{prefix}{key}: {value}")
                first = False
        casc_block = "\n".join(lines)
    frontmatter = "\n".join([
        'verifier_schema_version: "1.3"',
        f"id: {card_id}",
        f"title: Card {card_id}",
        "project: C:\\dev\\project-example",
        f"status: {status}",
        f"points: {points}",
        "stakes: low",
        "difficulty: shallow",
        "trace_id: 00000000-0000-0000-0000-000000000000",
        "cost_cap_usd: null",
        "estimated_tokens: 1000",
        "actual_tokens: null",
        deps_block,
        f"batch: {card_id.split('-')[0]}",
        "story_hash: deadbeef",
        "created: 2026-05-01",
        "started_at: null",
        "finished_at: null",
        "claimed_by: null",
        "last_heartbeat: null",
        f"branch: card/{card_id}",
        "base_branch: main",
        "merge_status: pending",
        "verified_at: null",
        "verified_by: null",
        "verifier_skipped_reason: null",
        casc_block,
        "verifier_cascade_history: []",
        "standup_reason: null",
    ])
    body_text = body if body is not None else textwrap.dedent(
        f"""\

        ## Context

        Synthetic card {card_id} for the storage-layer test suite.

        ## Scope

        - do the thing
        """
    )
    return f"---\n{frontmatter}\n---\n{body_text}"


# v1 status subfolders, mirrored from common.types.ALL_SUBFOLDERS.
V1_SUBFOLDERS: tuple[str, ...] = (
    "backlog",
    "active",
    "amendments",
    "awaiting_standup_review",
    "done",
    "blocked",
)


def build_v1_tree(root: Path, layout: dict[str, list[str]]) -> int:
    """Create a synthetic v1 TODO tree under `root`.

    `layout` maps a subfolder name to the list of card ids to drop in
    it. A card id ending `-escalated` gets a `cascade_history` entry
    so the history-to-events path is exercised. Returns the total
    number of card files written.
    """
    total = 0
    for subfolder in V1_SUBFOLDERS:
        (root / subfolder).mkdir(parents=True, exist_ok=True)
    for subfolder, card_ids in layout.items():
        for card_id in card_ids:
            cascade = None
            if card_id.endswith("-escalated"):
                cascade = [{
                    "from_tier": 2,
                    "to_tier": 3,
                    "reason": "low confidence",
                    "confidence_at_escalation": 0.4,
                    "at": "2026-05-02T10:00:00Z",
                }]
            text = make_card_text(card_id, status=subfolder, cascade_history=cascade)
            (root / subfolder / f"{card_id}.md").write_text(
                text, encoding="utf-8", newline=""
            )
            total += 1
    return total
