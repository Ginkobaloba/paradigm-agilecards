"""SdkInvoker: the real in-process executor.

Chunk 1 shipped `StubInvoker` (sleep, return a fake completion).
Chunk 2b-ii lands `SdkInvoker` at the same `Invoker` seam: it opens
an Anthropic SDK client in-process, runs the card through a real
model call, meters token spend against the card's `cost_cap_usd`,
and escalates the model tier when the executor's self-reported
confidence falls low (RUNNER_CONTRACT.md "Cascade-on-confidence
routing"). The daemon and `run_worker` do not change -- the seam is
the whole point.

Scope, stated plainly so it can be checked: the 2b-ii executor is
**reasoning-only**. It reads the card and produces a structured
completion report; it does not yet hold a tool belt (file edits,
shell, git). Tool-equipped execution intersects the verifier and the
merge gates and is chunk 3+. The cost-cap `before_tool` hook is
already wired and tested so a future tool loop inherits enforcement
for free. What 2b-ii proves live is the real machinery: a metered,
cost-capped, cascade-aware model call driving a card.

Context discipline (RUNNER_CONTRACT.md "Context discipline"): the
executor prompt carries only the card body, the trace id, and the
worktree path. No batch manifest, no sibling cards, no planning
conversation.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from ..common.types import now_utc_iso
from .cost import CostCapExceeded, CostGovernor, Pricing, model_tier
from .invoker import InvokeRequest, InvokeResult


log = logging.getLogger(__name__)


# Points -> model tier. A stand-in for the /cards skill's
# `tier_map_claude.yaml`, which is not vendored into the runner repo.
# RUNNER_CONTRACT.md keys the executor's planned tier on `card.points`
# (1-6); this is the runner-side default mapping until the canonical
# file is wired in (chunk 3+).
_POINTS_TO_TIER: dict[int, str] = {1: "haiku", 2: "haiku", 3: "sonnet",
                                   4: "sonnet", 5: "opus", 6: "opus"}

# Tier -> concrete model id used for the API call.
_TIER_MODEL: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

# The lowest `points` value whose tier is >= a given model floor.
_FLOOR_MIN_POINTS: dict[str, int] = {"haiku": 1, "sonnet": 3, "opus": 5}

# RUNNER_CONTRACT.md "Cascade-on-confidence routing": the escalation
# cap is 2 and MUST NOT exceed 2 in v1.2.
_MAX_ESCALATIONS_HARD_CAP = 2

_CONFIDENCE_RE = re.compile(r"CONFIDENCE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)

_SYSTEM_PROMPT = (
    "You are an autonomous software executor in the agile-cards "
    "runner. You receive exactly one work card and nothing else: no "
    "sibling cards, no batch manifest, no planning conversation. "
    "Read the card and produce a concise, honest completion report: "
    "what the work entails, how you would implement it, the surprises "
    "or risks you see, and a brief per-acceptance-criterion "
    "assessment. Be realistic and specific; do not pad. "
    "You are NOT being asked to write the code in this turn -- you are "
    "reporting on the work.\n\n"
    "End your reply with a final line in this EXACT form:\n"
    "CONFIDENCE: <n>\n"
    "where <n> is a number from 0.0 to 1.0 giving your confidence "
    "that the work as scoped is well understood and completable. Use "
    "a low value if the card is ambiguous, under-specified, or beyond "
    "the current tier's capability."
)


def _parse_cost_cap(value: Any) -> float | None:
    """Coerce a card's `cost_cap_usd` frontmatter value to float or None."""
    if value is None or value == "":
        return None
    try:
        cap = float(value)
    except (TypeError, ValueError):
        log.warning("unparseable cost_cap_usd %r; treating as no cap", value)
        return None
    return cap if cap > 0 else None


def _extract_confidence(text: str, *, default: float) -> float:
    """Pull the executor's self-reported confidence from its reply.

    Uses the last `CONFIDENCE: <n>` marker. A missing marker is not
    treated as low confidence -- escalating on a formatting slip would
    burn tokens for nothing -- so it falls back to `default` (1.0 in
    normal operation: settle, do not climb).
    """
    matches = _CONFIDENCE_RE.findall(text)
    if not matches:
        log.warning("executor reply carried no CONFIDENCE marker")
        return default
    try:
        value = float(matches[-1])
    except ValueError:
        return default
    return min(1.0, max(0.0, value))


def _strip_confidence_line(text: str) -> str:
    """Drop the trailing CONFIDENCE marker line from the displayed notes."""
    lines = text.rstrip().splitlines()
    while lines and _CONFIDENCE_RE.search(lines[-1]) and "CONFIDENCE" in lines[-1].upper():
        lines.pop()
    return "\n".join(lines).rstrip()


@dataclass
class SdkInvoker:
    """Real Anthropic-SDK-backed executor.

    `client` is injectable: tests pass a fake exposing
    `.messages.create(...)` so the whole suite runs token-free. In
    production it is left None and a real `anthropic.Anthropic` is
    built lazily from `api_key` (or the ambient `ANTHROPIC_API_KEY`).
    """

    api_key: str | None = None
    client: Any | None = None
    max_output_tokens: int = 2048
    cascade_threshold: float = 0.6
    max_escalations: int = _MAX_ESCALATIONS_HARD_CAP
    missing_confidence_default: float = 1.0
    pricing: Pricing | None = None

    def __post_init__(self) -> None:
        # The contract caps escalations at 2; clamp defensively in
        # case a project config or env var tries to raise it.
        self.max_escalations = max(0, min(_MAX_ESCALATIONS_HARD_CAP,
                                          self.max_escalations))

    @classmethod
    def from_env(cls) -> "SdkInvoker":
        """Build an SdkInvoker from environment configuration.

        The worker's `main_from_env` uses this. Knobs that would live
        in the per-project config (cascade threshold, output cap) are
        read from env vars until project config plumbing lands.
        """
        def _float(name: str, fallback: float) -> float:
            raw = os.environ.get(name)
            if not raw:
                return fallback
            try:
                return float(raw)
            except ValueError:
                log.warning("ignoring malformed %s=%r", name, raw)
                return fallback

        def _int(name: str, fallback: int) -> int:
            raw = os.environ.get(name)
            if not raw:
                return fallback
            try:
                return int(raw)
            except ValueError:
                log.warning("ignoring malformed %s=%r", name, raw)
                return fallback

        return cls(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            max_output_tokens=_int("CARDS_RUNNER_MAX_OUTPUT_TOKENS", 2048),
            cascade_threshold=_float("CARDS_RUNNER_CASCADE_THRESHOLD", 0.6),
            max_escalations=_int("CARDS_RUNNER_MAX_ESCALATIONS",
                                 _MAX_ESCALATIONS_HARD_CAP),
        )

    # ---- public seam --------------------------------------------------

    def invoke(self, request: InvokeRequest) -> InvokeResult:
        fm = request.snapshot.frontmatter
        cap = _parse_cost_cap(fm.get("cost_cap_usd"))
        governor = CostGovernor.create(cap, pricing=self.pricing)
        start_points = self._resolve_start_points(fm)
        system, user = self._build_prompt(request)
        est_input = self._estimate_input_tokens(system, user)

        escalations: list[dict[str, Any]] = []
        points = start_points
        transcript = ""
        confidence: float | None = None
        final_model = _TIER_MODEL[_POINTS_TO_TIER[points]]

        try:
            for step in range(self.max_escalations + 1):
                model = _TIER_MODEL[_POINTS_TO_TIER[points]]
                final_model = model
                governor.before_call(
                    model,
                    est_input_tokens=est_input,
                    max_output_tokens=self.max_output_tokens,
                )
                transcript, in_tok, out_tok = self._one_turn(
                    model, system, user
                )
                governor.record_call(model, in_tok, out_tok)
                confidence = _extract_confidence(
                    transcript, default=self.missing_confidence_default
                )
                log.info(
                    "executor step=%d points=%d model=%s confidence=%.2f "
                    "spent=$%.4f",
                    step, points, model, confidence, governor.meter.usd,
                )
                if confidence >= self.cascade_threshold:
                    break
                if step >= self.max_escalations or points >= 6:
                    break  # cascade exhausted; halt determined below.
                new_points = min(6, points + 1)
                escalations.append(
                    self._escalation_entry(
                        request, points, new_points, confidence
                    )
                )
                points = new_points
        except CostCapExceeded as exc:
            return self._halt_result(
                request, "cost_cap", governor, escalations, final_model,
                transcript, confidence, halt_detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - SDK / network / decode.
            log.exception("SdkInvoker model call failed")
            return self._error_result(
                request, governor, escalations, final_model, str(exc)
            )

        exhausted = (
            confidence is not None and confidence < self.cascade_threshold
        )
        halt_kind = "cascade_exhausted" if exhausted else None
        return self._final_result(
            request, governor, escalations, final_model, transcript,
            confidence, halt_kind,
        )

    # ---- model + prompt ----------------------------------------------

    def _resolve_start_points(self, fm: dict[str, Any]) -> int:
        """The card's planned tier, as a `points` value, floor-clamped."""
        raw = fm.get("points")
        try:
            points = int(raw) if raw is not None else 2
        except (TypeError, ValueError):
            points = 2
        points = max(1, min(6, points))
        floor = str(fm.get("model_floor") or "haiku").lower()
        floor_min = _FLOOR_MIN_POINTS.get(model_tier(floor), 1)
        return max(points, floor_min)

    def _build_prompt(self, request: InvokeRequest) -> tuple[str, str]:
        snap = request.snapshot
        title = snap.frontmatter.get("title") or snap.card_id
        user = (
            f"# Work card: {snap.card_id}\n"
            f"Title: {title}\n"
            f"trace_id: {request.trace_id}\n"
            f"worktree: {request.worktree}\n\n"
            "--- card body ---\n"
            f"{snap.body.strip()}\n"
            "--- end card body ---\n"
        )
        return _SYSTEM_PROMPT, user

    @staticmethod
    def _estimate_input_tokens(system: str, user: str) -> int:
        """A deliberately rough chars/4 estimate for the pre-call guard."""
        return (len(system) + len(user)) // 4 + 16

    # ---- the SDK call -------------------------------------------------

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - declared dep.
            raise RuntimeError(
                "the 'anthropic' package is required for SdkInvoker; "
                "install it with `pip install -e .[dev]`"
            ) from exc
        if not self.api_key:
            raise RuntimeError(
                "no ANTHROPIC_API_KEY available to the worker; the "
                "daemon injects it only in --invoker sdk mode"
            )
        self.client = anthropic.Anthropic(api_key=self.api_key)
        return self.client

    def _one_turn(
        self, model: str, system: str, user: str
    ) -> tuple[str, int, int]:
        """One model call. Returns (text, input_tokens, output_tokens)."""
        client = self._get_client()
        message = client.messages.create(
            model=model,
            max_tokens=self.max_output_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = self._text_of(message)
        usage = getattr(message, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        return text, in_tok, out_tok

    @staticmethod
    def _text_of(message: Any) -> str:
        """Join every text block of an Anthropic Message into one string."""
        parts: list[str] = []
        for block in getattr(message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                parts.append(str(getattr(block, "text", "")))
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts).strip()

    # ---- cascade bookkeeping -----------------------------------------

    def _escalation_entry(
        self,
        request: InvokeRequest,
        from_points: int,
        to_points: int,
        confidence: float,
    ) -> dict[str, Any]:
        """One `cascade_history` entry per RUNNER_CONTRACT.md shape.

        The contract shape is `{from_tier, to_tier, reason,
        confidence_at_escalation, at}`. `from_model` / `to_model` /
        `attempt_trace_id` are forensic additions: the daemon filters
        on `attempt_trace_id` so it emits one `escalated` event per
        escalation without double-counting on re-claim.
        """
        return {
            "from_tier": from_points,
            "to_tier": to_points,
            "from_model": _TIER_MODEL[_POINTS_TO_TIER[from_points]],
            "to_model": _TIER_MODEL[_POINTS_TO_TIER[to_points]],
            "reason": (
                f"executor confidence {confidence:.2f} below threshold "
                f"{self.cascade_threshold:.2f}"
            ),
            "confidence_at_escalation": round(confidence, 4),
            "at": now_utc_iso(),
            "attempt_trace_id": request.attempt_trace_id,
        }

    # ---- result assembly ---------------------------------------------

    def _final_result(
        self,
        request: InvokeRequest,
        governor: CostGovernor,
        escalations: list[dict[str, Any]],
        model: str,
        transcript: str,
        confidence: float | None,
        halt_kind: str | None,
    ) -> InvokeResult:
        report = _strip_confidence_line(transcript) or "(no executor output)"
        notes = self._notes(
            report, governor, escalations, confidence, model, halt_kind
        )
        return InvokeResult(
            completion_notes_markdown=notes,
            actual_tokens=governor.meter.total_tokens,
            model_used=model,
            success=halt_kind is None,
            actual_cost_usd=round(governor.meter.usd, 6),
            halt_kind=halt_kind,
            cascade_history=tuple(escalations),
            cost_snapshot=governor.meter.snapshot(),
        )

    def _halt_result(
        self,
        request: InvokeRequest,
        halt_kind: str,
        governor: CostGovernor,
        escalations: list[dict[str, Any]],
        model: str,
        transcript: str,
        confidence: float | None,
        *,
        halt_detail: str,
    ) -> InvokeResult:
        report = _strip_confidence_line(transcript)
        body = (
            f"**Executor halted: {halt_kind}.** {halt_detail}\n\n"
            f"Partial work before the halt is preserved on the card "
            f"branch. The runner routes this card to `blocked`.\n\n"
            + (f"Last executor output:\n\n{report}\n" if report else "")
        )
        notes = self._notes(
            body, governor, escalations, confidence, model, halt_kind
        )
        return InvokeResult(
            completion_notes_markdown=notes,
            actual_tokens=governor.meter.total_tokens,
            model_used=model,
            success=False,
            actual_cost_usd=round(governor.meter.usd, 6),
            halt_kind=halt_kind,
            cascade_history=tuple(escalations),
            cost_snapshot=governor.meter.snapshot(),
        )

    def _error_result(
        self,
        request: InvokeRequest,
        governor: CostGovernor,
        escalations: list[dict[str, Any]],
        model: str,
        error: str,
    ) -> InvokeResult:
        notes = self._notes(
            f"**Executor error.** The SDK call failed: {error}\n",
            governor, escalations, None, model, None,
        )
        return InvokeResult(
            completion_notes_markdown=notes,
            actual_tokens=governor.meter.total_tokens,
            model_used=model,
            success=False,
            actual_cost_usd=round(governor.meter.usd, 6),
            halt_kind=None,
            cascade_history=tuple(escalations),
            cost_snapshot=governor.meter.snapshot(),
        )

    def _notes(
        self,
        body: str,
        governor: CostGovernor,
        escalations: list[dict[str, Any]],
        confidence: float | None,
        model: str,
        halt_kind: str | None,
    ) -> str:
        meter = governor.meter
        lines = [
            body.rstrip(),
            "",
            "---",
            "",
            "### Executor run metadata",
            "",
            "- invoker: SdkInvoker (chunk 2b-ii, reasoning-only)",
            f"- final model: {model}",
            "- confidence: "
            + (f"{confidence:.2f}" if confidence is not None else "n/a"),
            f"- tokens: {meter.total_tokens} "
            f"(in {meter.input_tokens}, out {meter.output_tokens})",
            f"- model calls: {meter.calls}",
            f"- derived cost: ${meter.usd:.4f}"
            + (
                f" / cap ${governor.cap_usd:.4f}"
                if governor.cap_usd is not None
                else " (no cap set)"
            ),
            f"- escalations: {len(escalations)}",
        ]
        if halt_kind:
            lines.append(f"- halt: {halt_kind}")
        for entry in escalations:
            lines.append(
                f"  - tier {entry['from_tier']} -> {entry['to_tier']} "
                f"({entry['from_model']} -> {entry['to_model']}): "
                f"{entry['reason']}"
            )
        return "\n".join(lines) + "\n"
