"""SdkInvoker: the real in-process executor.

Chunk 1 shipped `StubInvoker` (sleep, return a fake completion).
Chunk 2b-ii lands `SdkInvoker` at the same `Invoker` seam: it opens
an Anthropic SDK client in-process, runs the card through a real
model call, meters token spend against the card's `cost_cap_usd`,
and escalates the model tier when the executor's self-reported
confidence falls low (RUNNER_CONTRACT.md "Cascade-on-confidence
routing"). The daemon and `run_worker` do not change -- the seam is
the whole point.

Chunk 3 grows the executor a tool belt. The `use_tools=True` mode
runs a multi-turn SDK tool-use loop bound to `worker_stub.tools.ToolBelt`,
so the executor can read and edit files, run shell commands, and run
safe git verbs inside the per-card worktree. The `before_tool`
cost-cap hook fires on every dispatch -- the meter wired in 2b-ii is
the same meter the tool loop reads from. Confidence is now reported
by the model calling the `report_done` tool (with its `confidence`
argument); a turn that ends `end_turn` without `report_done` defaults
confidence to the prior reasoning-only behavior (settle).

Reasoning-only mode (`use_tools=False`) is unchanged from 2b-ii:
single-shot, `CONFIDENCE:` marker line, no tool calls. Both modes
share the cascade machinery.

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

from pathlib import Path

from ..common.types import now_utc_iso
from .cost import CostCapExceeded, CostGovernor, Pricing, model_tier
from .invoker import InvokeRequest, InvokeResult
from .tools import TOOL_DESCRIPTORS, ToolBelt, ToolError, ToolResult


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


_TOOL_SYSTEM_PROMPT = (
    "You are an autonomous software executor in the agile-cards "
    "runner. You receive exactly one work card and a set of tools: "
    "file_read, file_write, file_replace, list_dir, shell, git, and "
    "report_done. You work inside a per-card git worktree; every "
    "path is relative to it. You do NOT receive sibling cards, the "
    "batch manifest, or any prior conversation.\n\n"
    "Drive the card to completion: read the acceptance criteria, "
    "inspect the worktree, edit files, run tests or lints via shell, "
    "stage and commit your changes with the `git` tool. When the "
    "work is complete, call the `report_done` tool with a one-paragraph "
    "summary and a `confidence` score from 0.0 to 1.0. `confidence` "
    "below 0.6 will trigger a cascade to a stronger model; use it "
    "honestly when the card is ambiguous or beyond your reach.\n\n"
    "Rules of engagement:\n"
    "- Push, pull, fetch, clone, and remote operations are refused; "
    "merge orchestration is the runner's job, not yours.\n"
    "- Keep diffs minimal and targeted. Do not refactor surrounding "
    "code, do not add features beyond the card's scope.\n"
    "- If you cannot proceed (missing dependency, ambiguous spec, "
    "incomplete worktree), call `report_done` with low confidence "
    "and explain in the summary; do NOT fabricate work."
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


def _block_type(block: Any) -> str | None:
    """Return a content-block's `type`, accepting SDK objects or dicts."""
    if isinstance(block, dict):
        value = block.get("type")
    else:
        value = getattr(block, "type", None)
    return None if value is None else str(value)


def _block_attr(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _blocks_to_dicts(blocks: list[Any]) -> list[dict[str, Any]]:
    """Render assistant content blocks back into the SDK's input shape.

    The SDK accepts the same shape it produces, but the response
    objects are not plain dicts; we serialize the bits we use so the
    next `messages.create` accepts our echo without trying to look up
    attributes on stand-in fakes.
    """
    out: list[dict[str, Any]] = []
    for b in blocks:
        btype = _block_type(b)
        if btype == "text":
            out.append({"type": "text", "text": _block_attr(b, "text") or ""})
        elif btype == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": _block_attr(b, "id") or "",
                    "name": _block_attr(b, "name") or "",
                    "input": _block_attr(b, "input") or {},
                }
            )
    return out


def _rough_tokens_of(messages: list[dict[str, Any]]) -> int:
    """A coarse chars/4 estimator over a messages list.

    Used only for the cost governor's pre-call worst-case projection.
    Wrong-by-50% is fine: the cap is computed against `max_tokens` on
    output, which dominates a small input-side miscount.
    """
    import json as _json

    try:
        chars = len(_json.dumps(messages, default=str))
    except Exception:  # noqa: BLE001
        chars = sum(
            len(str(m.get("content", ""))) for m in messages
        )
    return chars // 4


def _truncate_for_log(value: Any, *, max_chars: int = 2000) -> Any:
    """Compact a tool input/result for the per-attempt tool log."""
    import json as _json

    try:
        text = _json.dumps(value, default=str, sort_keys=True)
    except Exception:  # noqa: BLE001
        text = str(value)
    if len(text) <= max_chars:
        try:
            return _json.loads(text)
        except Exception:  # noqa: BLE001
            return text
    return text[:max_chars] + "...[truncated]..."


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
    # Chunk 3 additions. When `use_tools` is True the invoker runs the
    # multi-turn SDK tool-use loop bound to a `ToolBelt` rooted at the
    # request's worktree; the executor edits files, runs shell, and
    # commits inside that worktree. `max_tool_turns` is the hard cap
    # on tool-use rounds per cascade step to bound runaway loops; the
    # cost cap is the soft cap.
    use_tools: bool = False
    max_tool_turns: int = 24
    tool_env: dict[str, str] | None = None  # env block the tool belt's
                                            # shell/git tools inherit;
                                            # defaults to os.environ.

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

        use_tools = os.environ.get("CARDS_RUNNER_USE_TOOLS", "0").strip().lower()
        use_tools_bool = use_tools in ("1", "true", "yes", "on")
        return cls(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            max_output_tokens=_int("CARDS_RUNNER_MAX_OUTPUT_TOKENS", 2048),
            cascade_threshold=_float("CARDS_RUNNER_CASCADE_THRESHOLD", 0.6),
            max_escalations=_int("CARDS_RUNNER_MAX_ESCALATIONS",
                                 _MAX_ESCALATIONS_HARD_CAP),
            use_tools=use_tools_bool,
            max_tool_turns=_int("CARDS_RUNNER_MAX_TOOL_TURNS", 24),
        )

    # ---- public seam --------------------------------------------------

    def invoke(self, request: InvokeRequest) -> InvokeResult:
        fm = request.snapshot.frontmatter
        cap = _parse_cost_cap(fm.get("cost_cap_usd"))
        governor = CostGovernor.create(cap, pricing=self.pricing)
        start_points = self._resolve_start_points(fm)
        if self.use_tools:
            system = _TOOL_SYSTEM_PROMPT
            user = self._build_tool_user_prompt(request)
        else:
            system, user = self._build_prompt(request)
        est_input = self._estimate_input_tokens(system, user)

        escalations: list[dict[str, Any]] = []
        points = start_points
        transcript = ""
        confidence: float | None = None
        final_model = _TIER_MODEL[_POINTS_TO_TIER[points]]
        tool_log: list[dict[str, Any]] = []

        try:
            for step in range(self.max_escalations + 1):
                model = _TIER_MODEL[_POINTS_TO_TIER[points]]
                final_model = model
                if self.use_tools:
                    transcript, confidence, step_tool_log = self._run_tool_loop(
                        request=request,
                        system=system,
                        user=user,
                        model=model,
                        governor=governor,
                        est_input=est_input,
                    )
                    tool_log.extend(step_tool_log)
                else:
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
                    "spent=$%.4f tool_turns=%d",
                    step, points, model, confidence or 0.0,
                    governor.meter.usd, len(tool_log),
                )
                if confidence is not None and confidence >= self.cascade_threshold:
                    break
                if step >= self.max_escalations or points >= 6:
                    break  # cascade exhausted; halt determined below.
                new_points = min(6, points + 1)
                escalations.append(
                    self._escalation_entry(
                        request, points, new_points, confidence or 0.0
                    )
                )
                points = new_points
        except CostCapExceeded as exc:
            return self._halt_result(
                request, "cost_cap", governor, escalations, final_model,
                transcript, confidence, halt_detail=str(exc),
                tool_log=tool_log,
            )
        except Exception as exc:  # noqa: BLE001 - SDK / network / decode.
            log.exception("SdkInvoker model call failed")
            return self._error_result(
                request, governor, escalations, final_model, str(exc),
                tool_log=tool_log,
            )

        exhausted = (
            confidence is not None and confidence < self.cascade_threshold
        )
        halt_kind = "cascade_exhausted" if exhausted else None
        return self._final_result(
            request, governor, escalations, final_model, transcript,
            confidence, halt_kind, tool_log=tool_log,
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

    def _build_tool_user_prompt(self, request: InvokeRequest) -> str:
        snap = request.snapshot
        title = snap.frontmatter.get("title") or snap.card_id
        return (
            f"# Work card: {snap.card_id}\n"
            f"Title: {title}\n"
            f"trace_id: {request.trace_id}\n"
            f"worktree: {request.worktree}\n\n"
            "Use your tools to drive this card to completion, then "
            "call `report_done`.\n\n"
            "--- card body ---\n"
            f"{snap.body.strip()}\n"
            "--- end card body ---\n"
        )

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

    # ---- tool-use loop -----------------------------------------------

    def _run_tool_loop(
        self,
        *,
        request: InvokeRequest,
        system: str,
        user: str,
        model: str,
        governor: CostGovernor,
        est_input: int,
    ) -> tuple[str, float | None, list[dict[str, Any]]]:
        """Drive one cascade step's multi-turn tool-use loop.

        Returns `(transcript, confidence, tool_log)`. `confidence` is
        the value `report_done` returned when the model called it; if
        the model ended its turn another way, it is the
        `missing_confidence_default` (settle) -- the same fallback the
        reasoning-only path uses. The tool log is a per-call record
        the SdkInvoker stitches into completion notes.
        """
        belt = self._build_tool_belt(request)
        client = self._get_client()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user}
        ]
        tool_log: list[dict[str, Any]] = []
        text_parts: list[str] = []
        confidence: float | None = None
        terminal = False

        for turn in range(self.max_tool_turns):
            governor.before_call(
                model,
                est_input_tokens=est_input + _rough_tokens_of(messages),
                max_output_tokens=self.max_output_tokens,
            )
            message = client.messages.create(
                model=model,
                max_tokens=self.max_output_tokens,
                system=system,
                tools=list(TOOL_DESCRIPTORS),
                messages=messages,
            )
            usage = getattr(message, "usage", None)
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            governor.record_call(model, in_tok, out_tok)

            stop_reason = getattr(message, "stop_reason", None)
            content_blocks = list(getattr(message, "content", []) or [])
            text_chunk = self._text_of(message)
            if text_chunk:
                text_parts.append(text_chunk)

            tool_uses = [b for b in content_blocks if _block_type(b) == "tool_use"]
            if not tool_uses:
                # Pure-text turn. End the loop; confidence falls back
                # to either an embedded marker (defense in depth) or
                # the missing-marker default.
                if text_chunk:
                    parsed = _extract_confidence(
                        text_chunk, default=-1.0  # sentinel: no marker.
                    )
                    if parsed >= 0:
                        confidence = parsed
                break

            # Echo the assistant turn back into the conversation so the
            # tool_result blocks have something to reference. The SDK
            # accepts the raw content blocks back; convert to dicts.
            messages.append(
                {"role": "assistant", "content": _blocks_to_dicts(content_blocks)}
            )

            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                name = _block_attr(tu, "name") or ""
                tool_input = _block_attr(tu, "input") or {}
                tool_id = _block_attr(tu, "id") or ""
                if not isinstance(tool_input, dict):
                    tool_input = {}
                try:
                    governor.before_tool(name)
                except CostCapExceeded:
                    raise
                try:
                    result = belt.execute(name, tool_input)
                    result_payload = result.payload
                    is_error = not result.ok
                except ToolError as exc:
                    result_payload = {"error": str(exc), "refused": True}
                    is_error = True
                    result = ToolResult(False, result_payload)
                except Exception as exc:  # noqa: BLE001 - defensive.
                    log.exception("tool %s crashed", name)
                    result_payload = {"error": f"tool crashed: {exc}"}
                    is_error = True
                    result = ToolResult(False, result_payload)

                tool_log.append(
                    {
                        "turn": turn,
                        "name": name,
                        "ok": result.ok,
                        "input": _truncate_for_log(tool_input),
                        "result": _truncate_for_log(result_payload),
                    }
                )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": is_error,
                        "content": result.to_text(),
                    }
                )

                if name == "report_done" and result.ok:
                    terminal = True
                    confidence = float(result.payload.get("confidence", 0.0))
                    text_parts.append(
                        f"\nreport_done: {result.payload.get('summary', '')}"
                    )

            messages.append({"role": "user", "content": tool_results})

            if terminal:
                break
            if stop_reason == "end_turn":
                # Model ended turn without calling report_done. Treat
                # it as a settle at the missing-confidence default,
                # matching the reasoning-only fallback.
                break

        if confidence is None:
            confidence = self.missing_confidence_default
        transcript = "\n".join(text_parts).strip()
        return transcript, confidence, tool_log

    def _build_tool_belt(self, request: InvokeRequest) -> ToolBelt:
        env = (
            self.tool_env
            if self.tool_env is not None
            else dict(os.environ)
        )
        worktree = Path(request.worktree)
        return ToolBelt(worktree=worktree, env=env)

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
        *,
        tool_log: list[dict[str, Any]] | None = None,
    ) -> InvokeResult:
        report = _strip_confidence_line(transcript) or "(no executor output)"
        notes = self._notes(
            report, governor, escalations, confidence, model, halt_kind,
            tool_log=tool_log or [],
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
        tool_log: list[dict[str, Any]] | None = None,
    ) -> InvokeResult:
        report = _strip_confidence_line(transcript)
        body = (
            f"**Executor halted: {halt_kind}.** {halt_detail}\n\n"
            f"Partial work before the halt is preserved on the card "
            f"branch. The runner routes this card to `blocked`.\n\n"
            + (f"Last executor output:\n\n{report}\n" if report else "")
        )
        notes = self._notes(
            body, governor, escalations, confidence, model, halt_kind,
            tool_log=tool_log or [],
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
        *,
        tool_log: list[dict[str, Any]] | None = None,
    ) -> InvokeResult:
        notes = self._notes(
            f"**Executor error.** The SDK call failed: {error}\n",
            governor, escalations, None, model, None,
            tool_log=tool_log or [],
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
        *,
        tool_log: list[dict[str, Any]] | None = None,
    ) -> str:
        meter = governor.meter
        mode = "tool-using" if self.use_tools else "reasoning-only"
        lines = [
            body.rstrip(),
            "",
            "---",
            "",
            "### Executor run metadata",
            "",
            f"- invoker: SdkInvoker ({mode})",
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
        if tool_log:
            lines.append(f"- tool calls: {len(tool_log)}")
        for entry in escalations:
            lines.append(
                f"  - tier {entry['from_tier']} -> {entry['to_tier']} "
                f"({entry['from_model']} -> {entry['to_model']}): "
                f"{entry['reason']}"
            )
        if tool_log:
            lines.extend([
                "",
                "### Tool call log",
                "",
            ])
            for entry in tool_log[:48]:  # keep notes readable.
                ok_marker = "ok" if entry["ok"] else "FAIL"
                lines.append(
                    f"- turn {entry['turn']}: {entry['name']} ({ok_marker})"
                )
            if len(tool_log) > 48:
                lines.append(f"- ... {len(tool_log) - 48} more calls")
        return "\n".join(lines) + "\n"
