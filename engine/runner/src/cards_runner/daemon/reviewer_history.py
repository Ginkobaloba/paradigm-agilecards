"""Append-only history of reviewer + amendment-editor decisions.

Chunk 6d. The per-card markers under `signals/sibling_reviews/<id>.json`
are runtime de-dup -- the chunk 6d cleanup sweep deletes them once the
card reaches a terminal state. But the *historical* record (what did
the reviewer approve last week? which models did the amendment editor
use this month?) needs to survive cleanup. This module owns that log.

Shape:

- One JSONL file per todo root: `signals/reviewer_history.jsonl`.
- Append-only. The runner never edits or removes lines.
- Each line is one decision (sibling review, amendment review,
  amendment editor) with a stable schema documented on `HistoryEntry`.
- The file is the only durable record of reviewer behavior; treat it
  the way you'd treat a Git audit log.

Reading: `read_history(path, since=..., kind=..., decision=...)`
loads + filters. The full file fits in memory at reasonable runner
scale (one entry per reviewer call; a busy runner does dozens per day,
not millions). A read filter is a list comprehension, not a real query
engine; if you need that, sync the JSONL into Dolt.

Appends are best-effort: a write failure logs at WARNING but never
raises into the reviewer's call path. The marker file is still the
authoritative per-call record; the JSONL is a denormalization for
historical query convenience.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from ..common.types import RuntimePaths, now_utc_iso


log = logging.getLogger(__name__)


HISTORY_FILENAME: str = "reviewer_history.jsonl"


HistoryKind = Literal["sibling_review", "amendment_review", "amendment_edit"]


@dataclass(frozen=True)
class HistoryEntry:
    """One reviewer-or-editor decision, suitable for JSONL serialization.

    Field reference:

    - `kind`: which subsystem produced this entry. The reviewer's
      decision and the editor's decision are recorded as separate
      entries so a query can filter cleanly.
    - `decision`: for `sibling_review` and `amendment_review`, one of
      `approve` / `request_changes` / `comment`. For `amendment_edit`,
      always `applied` (the editor only writes a history entry when
      it actually splices; failed/declined edits aren't recorded
      because they don't represent a successful decision).
    - `pr_url`: present for sibling_review; None for amendment paths.
    - `ac_index`: present for amendment_edit; None for the others.
    - `actual_cost_usd` / `input_tokens` / `output_tokens` /
      `model_used`: cost attribution per chunk 6b.
    """

    at: str
    card_id: str
    kind: HistoryKind
    decision: str
    reviewer_label: str
    confidence: float = 0.0
    model_used: str = ""
    pr_url: str | None = None
    ac_index: int | None = None
    amendment_reason: str | None = None
    actual_cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def history_path(paths: RuntimePaths) -> Path:
    """Resolve the JSONL location under the todo root."""
    return paths.signals / HISTORY_FILENAME


def append_entry(paths: RuntimePaths, entry: HistoryEntry) -> bool:
    """Append one entry to the history JSONL. Returns False on failure.

    The reviewer caller logs the failure via the return value but does
    NOT raise -- a history append is non-load-bearing.
    """
    path = history_path(paths)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(entry.to_jsonl() + "\n")
        return True
    except OSError as exc:
        log.warning("could not append reviewer history entry to %s: %s", path, exc)
        return False


def read_history(
    paths: RuntimePaths,
    *,
    since: str | None = None,
    kind: HistoryKind | None = None,
    decision: str | None = None,
    card_id: str | None = None,
) -> list[HistoryEntry]:
    """Load and filter the history JSONL.

    `since` is an ISO 8601 timestamp (matches what `now_utc_iso()`
    writes); entries with `at >= since` are kept. The other filters
    are equality on the named field. Multiple filters AND together.
    """
    path = history_path(paths)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.warning("could not read reviewer history at %s: %s", path, exc)
        return []
    out: list[HistoryEntry] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            log.warning("malformed reviewer-history line skipped")
            continue
        if not isinstance(data, dict):
            continue
        entry = _from_dict(data)
        if entry is None:
            continue
        if since is not None and entry.at < since:
            continue
        if kind is not None and entry.kind != kind:
            continue
        if decision is not None and entry.decision != decision:
            continue
        if card_id is not None and entry.card_id != card_id:
            continue
        out.append(entry)
    return out


def _from_dict(data: dict[str, Any]) -> HistoryEntry | None:
    """Defensive reconstruction. Skips lines missing required fields."""
    try:
        return HistoryEntry(
            at=str(data["at"]),
            card_id=str(data["card_id"]),
            kind=data["kind"],  # type: ignore[arg-type]
            decision=str(data["decision"]),
            reviewer_label=str(data.get("reviewer_label") or ""),
            confidence=float(data.get("confidence") or 0.0),
            model_used=str(data.get("model_used") or ""),
            pr_url=(str(data["pr_url"]) if data.get("pr_url") else None),
            ac_index=(int(data["ac_index"]) if data.get("ac_index") is not None else None),
            amendment_reason=(
                str(data["amendment_reason"]) if data.get("amendment_reason") else None
            ),
            actual_cost_usd=(
                float(data["actual_cost_usd"])
                if data.get("actual_cost_usd") is not None else None
            ),
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            extra=dict(data.get("extra") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("skipping malformed history entry: %s", exc)
        return None


def aggregate(
    entries: Iterable[HistoryEntry],
) -> dict[str, Any]:
    """Tiny summary: counts by kind + decision, total spend.

    Returned shape:

        {
          "total_entries": int,
          "by_kind": {kind: count, ...},
          "by_decision": {decision: count, ...},
          "by_kind_decision": {kind: {decision: count, ...}, ...},
          "total_input_tokens": int,
          "total_output_tokens": int,
          "total_cost_usd": float,
        }
    """
    items = list(entries)
    by_kind: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    by_kind_decision: dict[str, dict[str, int]] = {}
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    for e in items:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        by_decision[e.decision] = by_decision.get(e.decision, 0) + 1
        per = by_kind_decision.setdefault(e.kind, {})
        per[e.decision] = per.get(e.decision, 0) + 1
        input_tokens += e.input_tokens
        output_tokens += e.output_tokens
        if e.actual_cost_usd is not None:
            cost += e.actual_cost_usd
    return {
        "total_entries": len(items),
        "by_kind": by_kind,
        "by_decision": by_decision,
        "by_kind_decision": by_kind_decision,
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "total_cost_usd": round(cost, 6),
    }


def utc_now_iso() -> str:
    """Convenience re-export so callers don't need to import types.py."""
    return now_utc_iso()
