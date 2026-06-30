"""Card frontmatter read and write for the per-run projected card file.

A card is YAML-frontmatter Markdown. After the chunk 2b cutover the
database is canonical: a card's authoritative state is a row in the
card store. The runner projects a claimed card into the per-attempt
run dir as a `.md` file, and the worker reads and edits that file
exactly as a v1 worker edited a card in `active/`.

That projected file is an ephemeral per-run view, not a tracked
artifact anyone diffs. So the chunk 1 surgical in-place rewriter --
which existed to keep planner-owned frontmatter byte-identical and
produce clean git diffs -- is gone. `write_card_file` now does a
plain full-frontmatter dump. The repository owns durable writes; the
most fragile component in the v1 runner no longer has to learn to
serialize list-typed history fields. (See `storage_substrate_v2.md`
section 5.5 / 5.7.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .atomic import atomic_write_text
from .types import CardSnapshot


# A card looks like:
#
# ---
# id: b001-03-add-rate-limit-middleware
# status: backlog
# ...
# ---
#
# ## Context
# ...
#
# We split on the first two `---` lines.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


class CardParseError(Exception):
    """Raised when a card file is not a valid YAML-frontmatter Markdown doc."""


@dataclass(frozen=True)
class _Match:
    fm_text: str
    body: str


def _split_card(text: str) -> _Match:
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise CardParseError(
            "card file missing YAML frontmatter fenced by '---' lines"
        )
    return _Match(fm_text=m.group("fm"), body=m.group("body"))


def parse_card_file(path: Path) -> CardSnapshot:
    """Read and parse a card file. Returns a snapshot.

    `card_id` is taken from the `id:` field if present; otherwise the
    filename stem is used as a fallback.
    """
    text = path.read_text(encoding="utf-8")
    split = _split_card(text)
    fm: dict[str, Any] = yaml.safe_load(split.fm_text) or {}
    if not isinstance(fm, dict):
        raise CardParseError(
            f"frontmatter of {path} parsed to {type(fm).__name__}, "
            "expected mapping"
        )
    card_id = str(fm.get("id") or path.stem)
    return CardSnapshot(
        card_id=card_id,
        frontmatter=fm,
        body=split.body,
        raw_frontmatter_text=split.fm_text,
    )


def write_card_file(path: Path, snapshot: CardSnapshot) -> None:
    """Write a snapshot back atomically.

    The frontmatter is dumped whole with `yaml.safe_dump`. This is the
    deliberate replacement for the v1 surgical rewriter: the projected
    card file is an ephemeral per-run view, so key reordering and
    quoting churn no longer matter -- nobody diffs this file, and the
    runner re-parses it into the store on worker exit. A full dump
    also means the worker can write any field (including the previously
    unwriteable list-typed history fields) without special casing.
    """
    fm_text = yaml.safe_dump(
        snapshot.frontmatter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).rstrip("\n")
    rebuilt = f"---\n{fm_text}\n---\n{snapshot.body}"
    atomic_write_text(path, rebuilt)


def append_completion_notes(snapshot: CardSnapshot, notes_markdown: str) -> None:
    """Append a `## Completion notes` section to the card body.

    Idempotent for the section header: if the body already contains a
    `## Completion notes` line, the new content is concatenated under
    it. Otherwise the section is added at the end of the body.
    """
    header = "## Completion notes"
    body = snapshot.body.rstrip("\n")
    if header in body:
        snapshot.body = body + "\n\n" + notes_markdown.rstrip("\n") + "\n"
    else:
        snapshot.body = (
            body
            + "\n\n"
            + header
            + "\n\n"
            + notes_markdown.rstrip("\n")
            + "\n"
        )
