"""Card-event derivation — the Python port of legacy ``events/derive.ts``.

Pure function: diff the previous card snapshot against the current one and
return the events that happened. The event ``type`` strings and details
shapes are wire-contract (the Timeline UI renders them); see
docs/board/CARDS_API_CONTRACT.md §"Card events".

Timestamp rule (as legacy): an event whose trigger field carries a parseable
timestamp uses it; everything else falls back to the card's modification
time. One deliberate clarification vs legacy: for a brand-new card
(``prev is None``) we emit ``discovered`` and then apply the remaining rules
against an empty previous frontmatter, so a card that arrives already
claimed/finished still gets its lifecycle events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

FrontMatter = dict[str, Any]


@dataclass(frozen=True)
class CardSnapshot:
    status: str
    frontmatter: FrontMatter


@dataclass(frozen=True)
class DerivedEvent:
    type: str
    at: datetime
    details: Any = field(default=None)


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return bool(value)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _at(fm: FrontMatter, key: str, fallback: datetime) -> datetime:
    return _parse_ts(fm.get(key)) or fallback


def derive_events(
    prev: CardSnapshot | None,
    current: CardSnapshot,
    fallback_at: datetime,
) -> list[DerivedEvent]:
    events: list[DerivedEvent] = []
    cur = current.frontmatter

    if prev is None:
        events.append(
            DerivedEvent("discovered", fallback_at, {"status": current.status})
        )
        old = CardSnapshot(status=current.status, frontmatter={})
    else:
        old = prev
        if old.status != current.status:
            events.append(
                DerivedEvent(
                    "status_changed",
                    fallback_at,
                    {"from": old.status, "to": current.status},
                )
            )
    p = old.frontmatter

    if not _nonempty(p.get("claimed_by")) and _nonempty(cur.get("claimed_by")):
        events.append(
            DerivedEvent(
                "started",
                _at(cur, "claimed_at", fallback_at),
                {
                    "by": cur.get("claimed_by"),
                    "model": cur.get("model_used") or cur.get("model"),
                },
            )
        )
    if _nonempty(p.get("claimed_by")) and not _nonempty(cur.get("claimed_by")):
        events.append(
            DerivedEvent("released", fallback_at, {"from": p.get("claimed_by")})
        )

    hb = cur.get("last_heartbeat")
    if _nonempty(hb) and hb != p.get("last_heartbeat"):
        events.append(
            DerivedEvent(
                "heartbeat",
                _at(cur, "last_heartbeat", fallback_at),
                {"by": cur.get("claimed_by")},
            )
        )

    if not _nonempty(p.get("finished_at")) and _nonempty(cur.get("finished_at")):
        tokens = cur.get("actual_tokens")
        events.append(
            DerivedEvent(
                "finished",
                _at(cur, "finished_at", fallback_at),
                {
                    "tokens": tokens if isinstance(tokens, (int, float)) else None,
                    "model": cur.get("model_used") or cur.get("model"),
                },
            )
        )

    if not _nonempty(p.get("verified_at")) and _nonempty(cur.get("verified_at")):
        events.append(
            DerivedEvent(
                "verifier_called",
                _at(cur, "verified_at", fallback_at),
                {"by": cur.get("verified_by")},
            )
        )

    prev_cascade = p.get("cascade_history")
    cur_cascade = cur.get("cascade_history")
    prev_len = len(prev_cascade) if isinstance(prev_cascade, list) else 0
    if isinstance(cur_cascade, list) and len(cur_cascade) > prev_len:
        for entry in cur_cascade[prev_len:]:
            at = fallback_at
            if isinstance(entry, dict):
                at = _parse_ts(entry.get("at")) or fallback_at
            events.append(DerivedEvent("cascade", at, entry))

    merge_status = cur.get("merge_status")
    if _nonempty(merge_status) and merge_status != p.get("merge_status"):
        events.append(
            DerivedEvent(
                "merge_status_changed",
                fallback_at,
                {"from": p.get("merge_status"), "to": merge_status},
            )
        )

    return events
