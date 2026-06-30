"""Append-only metrics event log (ledger chunk 2).

`docs/design/throughput_metrics_ledger.md` section 3.2 and 5.3: every
write to `card_metrics` is preceded by an append to
`signals/metrics_events.jsonl`. The row is a denormalization; the log
is the authoritative record. A `card_metrics` row can always be rebuilt
from the log (`writer.fold_events`), which is what the section-12.3
replay verification checks.

Shape, deliberately mirroring chunk 6d `reviewer_history.py`:

- One JSONL file per todo root: `signals/metrics_events.jsonl`.
- Append-only. The runner never edits or removes lines.
- Each line is one lifecycle transition with a stable schema
  documented on `MetricsEvent`.
- Appends are best-effort: a write failure logs at WARNING and returns
  False, never raising into the daemon's call path.

Idempotency (spec section 5.4): the log tolerates duplicate events (a
crash-replayed worker exit appends its event twice). The fold in
`writer.py` deduplicates accumulating events by `(kind, dedup_key)`, so
re-processing the same transition does not double-count cumulative
fields. Every accumulating event therefore MUST carry a stable
`dedup_key` (the attempt trace id for per-attempt events, the card id
for last-wins events, the regressing card id for regression flags).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..common.types import RuntimePaths

log = logging.getLogger(__name__)

EVENTS_FILENAME: str = "metrics_events.jsonl"

# Event kinds. The fold in writer.py keys its handling off these.
KIND_CARD_CREATED: str = "card_created"
KIND_CARD_STARTED: str = "card_started"
KIND_EXECUTOR_EXITED: str = "executor_exited"
KIND_VERIFIER_DECIDED: str = "verifier_decided"
KIND_REWORK_TRIGGERED: str = "rework_triggered"
KIND_REVIEWER_SPEND: str = "reviewer_spend"
KIND_PR_OPENED: str = "pr_opened"
KIND_PR_MERGED: str = "pr_merged"
KIND_REGRESSION_FLAGGED: str = "regression_flagged"
KIND_CONTRACT_OUTCOME: str = "contract_outcome"
# Gate chunk 2b: the confidence gate's shadow-mode decision. Recorded to
# the log for later calibration (gate-3); the card_metrics fold ignores
# it (no gate columns in the chunk-1 schema).
KIND_GATE_SHADOW_DECISION: str = "gate_shadow_decision"
# Gate chunk 3: ramp lifecycle events (spec section 7.2). The phase
# kinds are emitted by `stats ramp advance`; the live-decision and
# kill-switch kinds are defined now so the chunk-3 readers (live-count,
# kill-switch-quiet checks in `metrics/ramp.py`) recognize them the
# moment chunk-4 live-mode wiring starts emitting them. None of these
# participate in the card_metrics fold.
KIND_GATE_PHASE_ADVANCED: str = "gate_phase_advanced"
KIND_GATE_PHASE_RECOMMENDATION: str = "gate_phase_recommendation"
KIND_GATE_LIVE_DECISION: str = "gate_live_decision"
KIND_GATE_KILLSWITCH_TRIPPED: str = "gate_killswitch_tripped"
KIND_GATE_KILLSWITCH_CLEARED: str = "gate_killswitch_cleared"

ALL_KINDS: tuple[str, ...] = (
    KIND_CARD_CREATED,
    KIND_CARD_STARTED,
    KIND_EXECUTOR_EXITED,
    KIND_VERIFIER_DECIDED,
    KIND_REWORK_TRIGGERED,
    KIND_REVIEWER_SPEND,
    KIND_PR_OPENED,
    KIND_PR_MERGED,
    KIND_REGRESSION_FLAGGED,
    KIND_CONTRACT_OUTCOME,
    KIND_GATE_SHADOW_DECISION,
    KIND_GATE_PHASE_ADVANCED,
    KIND_GATE_PHASE_RECOMMENDATION,
    KIND_GATE_LIVE_DECISION,
    KIND_GATE_KILLSWITCH_TRIPPED,
    KIND_GATE_KILLSWITCH_CLEARED,
)


@dataclass(frozen=True)
class MetricsEvent:
    """One metric-relevant lifecycle transition.

    - `at`: ISO 8601 UTC timestamp (second resolution, matches the rest
      of the runner).
    - `card_id` / `tenant_id`: the card this transition belongs to.
    - `kind`: one of the `KIND_*` constants.
    - `dedup_key`: identity used by the fold to deduplicate accumulating
      events. Per-attempt events use the attempt trace id; last-wins
      events use the card id; regression flags use the regressing card
      id. Two events with the same `(kind, dedup_key)` are the same
      transition replayed, not two distinct transitions.
    - `payload`: kind-specific fields the fold reads (tokens, timestamps,
      diff stats, gate outcome, etc.).
    """

    at: str
    card_id: str
    tenant_id: str
    kind: str
    dedup_key: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_jsonl(cls, line: str) -> "MetricsEvent":
        data = json.loads(line)
        return cls(
            at=str(data["at"]),
            card_id=str(data["card_id"]),
            tenant_id=str(data["tenant_id"]),
            kind=str(data["kind"]),
            dedup_key=str(data["dedup_key"]),
            payload=dict(data.get("payload", {})),
        )


def events_path(paths: RuntimePaths) -> Path:
    """Resolve the JSONL location under the todo root."""
    return paths.signals / EVENTS_FILENAME


def append_event(paths: RuntimePaths, event: MetricsEvent) -> bool:
    """Append one event to the metrics log. Returns False on failure.

    Best-effort by contract: a failed append logs at WARNING and is
    swallowed so the daemon's lifecycle sweep is never aborted by a
    metrics write (spec section 5.2 best-effort-with-log convention)."""
    path = events_path(paths)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(event.to_jsonl() + "\n")
        return True
    except OSError as exc:
        log.warning("could not append metrics event to %s: %s", path, exc)
        return False


def read_events(paths: RuntimePaths) -> list[MetricsEvent]:
    """Read every event in the log, in file order.

    A missing log is an empty list (the writer has not run yet). A
    malformed line is skipped with a WARNING rather than aborting the
    read -- one corrupt line should not make the whole log unreadable.
    """
    path = events_path(paths)
    if not path.exists():
        return []
    events: list[MetricsEvent] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(MetricsEvent.from_jsonl(line))
            except (ValueError, KeyError) as exc:
                log.warning(
                    "skipping malformed metrics event at %s:%d: %s",
                    path, lineno, exc,
                )
    return events


def read_events_for_card(
    paths: RuntimePaths, *, card_id: str, tenant_id: str
) -> list[MetricsEvent]:
    """Return only the events belonging to one card, in file order."""
    return [
        e for e in read_events(paths)
        if e.card_id == card_id and e.tenant_id == tenant_id
    ]


__all__ = [
    "ALL_KINDS",
    "EVENTS_FILENAME",
    "KIND_CARD_CREATED",
    "KIND_CARD_STARTED",
    "KIND_CONTRACT_OUTCOME",
    "KIND_EXECUTOR_EXITED",
    "KIND_GATE_KILLSWITCH_CLEARED",
    "KIND_GATE_KILLSWITCH_TRIPPED",
    "KIND_GATE_LIVE_DECISION",
    "KIND_GATE_PHASE_ADVANCED",
    "KIND_GATE_PHASE_RECOMMENDATION",
    "KIND_GATE_SHADOW_DECISION",
    "KIND_PR_MERGED",
    "KIND_PR_OPENED",
    "KIND_REGRESSION_FLAGGED",
    "KIND_REVIEWER_SPEND",
    "KIND_REWORK_TRIGGERED",
    "KIND_VERIFIER_DECIDED",
    "MetricsEvent",
    "append_event",
    "events_path",
    "read_events",
    "read_events_for_card",
]
