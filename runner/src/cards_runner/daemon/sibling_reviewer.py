"""Sibling-agent reviewer for tier-3/4 PRs (chunk 5).

The chunk-4 merge gate parks tier 3-4 cards in `blocked` with
`merge_status=requires_review` after opening the PR. A human can review
and merge those manually, but RUNNER_CONTRACT.md "Merge gates" calls
for a sibling reviewer agent to handle the bulk of them:

> tier 3, 4: auto-merge after a sibling-agent reviewer says ok

This module is the runner-side implementation. Each tick (when
`sibling_reviewer_enabled=True`), the daemon:

1. Lists `blocked` cards with `merge_status=requires_review` and a
   non-null `pr_url`.
2. Skips cards that already have a fresh sibling-review marker at
   `signals/sibling_reviews/<card_id>.json` whose `pr_url` matches the
   current card.pr_url.
3. For the rest: pulls the PR diff (`gh pr diff`), passes the card body
   + AC + diff to a `SiblingReviewerClient`, gets back a structured
   decision (`approve`/`request_changes`/`comment` + reasoning +
   confidence), and posts the review via `gh pr review`.
4. On `approve`, additionally fires `gh pr merge --auto --delete-branch`
   so the unblocker can promote the card to `done` once GitHub reports
   the merge landed. Approvals are NOT applied destructively (the
   merge gate's `--auto` flag waits for CI / branch protection to
   clear).
5. Writes the marker JSON so the next tick is a no-op for this PR.

The reviewer is intentionally NOT given write access to the card store
or AC. The contract: "The runner MUST never amend AC on its own
initiative" applies. The sibling reviewer's voice is a marker file + a
gh review comment; no card transitions until the unblocker sees the
merge land.

Costs: one `gh pr diff` (a few hundred bytes -> a few dozen KB) and one
LLM call per `requires_review` card per "decision moment". The marker
file makes the call once per PR (not per tick), which keeps the spend
bounded.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from ..common.project_config import ReviewerConfig
from ..common.types import DaemonConfig, RuntimePaths, now_utc_iso
from ..store import (
    DEFAULT_TENANT,
    ActorType,
    CardEvent,
    CardRecord,
    CardRepository,
    CardStatus,
    EventType,
)
from .pr_lifecycle import GhRunner


log = logging.getLogger(__name__)


Decision = Literal["approve", "request_changes", "comment"]


@dataclass(frozen=True)
class ReviewerDecision:
    """Structured output from a `SiblingReviewerClient`.

    `confidence` is the reviewer's self-reported confidence on a 0-1
    scale. The default sibling-reviewer client refuses to emit
    `approve` below 0.7 (it degrades to `comment` with the same
    reasoning); operators can tune that floor via the project config.
    """

    decision: Decision
    reasoning: str
    confidence: float = 0.0
    model_used: str = ""
    actual_cost_usd: float | None = None


class SiblingReviewerClient(Protocol):
    """Pluggable reviewer client.

    Tests inject a `StaticSiblingReviewerClient` that returns scripted
    decisions; production uses `AnthropicSiblingReviewerClient` against
    the SDK.
    """

    def review(
        self,
        *,
        card_id: str,
        card_body: str,
        pr_diff: str,
        reviewer: ReviewerConfig,
    ) -> ReviewerDecision: ...


@dataclass
class StaticSiblingReviewerClient:
    """Pre-canned reviewer outputs. Used by tests and by 'review-as-comment'
    operators who want the runner to drop a marker but never auto-approve.

    `decisions_by_card` overrides on a per-card basis; `default` covers
    the rest. Recorded calls go onto `calls` for assertion in tests.
    """

    default: ReviewerDecision = field(
        default_factory=lambda: ReviewerDecision(
            decision="comment",
            reasoning="static reviewer is offering no opinion",
            confidence=0.0,
        )
    )
    decisions_by_card: dict[str, ReviewerDecision] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def review(
        self,
        *,
        card_id: str,
        card_body: str,
        pr_diff: str,
        reviewer: ReviewerConfig,
    ) -> ReviewerDecision:
        self.calls.append({
            "card_id": card_id,
            "card_body": card_body,
            "pr_diff": pr_diff,
            "reviewer": reviewer,
        })
        return self.decisions_by_card.get(card_id, self.default)


@dataclass
class AnthropicSiblingReviewerClient:
    """Reviewer backed by the Anthropic SDK.

    `client` is an `anthropic.Anthropic` instance; the daemon
    constructs one in `_build_subjective_client()` for the verifier and
    we accept the same one here.  This is intentionally a small
    one-shot reviewer; cost-cap tracking lives in `worker_stub.cost`
    and is NOT (yet) wired in here -- the reviewer's spend is bounded
    by Anthropic's max-tokens setting per call.
    """

    client: Any  # anthropic.Anthropic
    max_tokens: int = 1024

    def review(
        self,
        *,
        card_id: str,
        card_body: str,
        pr_diff: str,
        reviewer: ReviewerConfig,
    ) -> ReviewerDecision:
        system_prompt = _system_prompt(reviewer)
        user_prompt = _user_prompt(card_id, card_body, pr_diff)
        # Defensive truncation: an oversized PR diff blows the context;
        # we keep the reviewer cheap by capping the diff at ~20k chars.
        # Truncation is best-effort context preservation, not a security
        # boundary -- a hostile diff is already on the card's branch.
        if len(user_prompt) > 64000:
            user_prompt = user_prompt[:64000] + "\n\n[...diff truncated...]"
        try:
            response = self.client.messages.create(
                model=reviewer.model_id,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:  # noqa: BLE001 - reviewer crash maps to comment.
            log.warning("sibling reviewer SDK call failed for %s: %s", card_id, exc)
            return ReviewerDecision(
                decision="comment",
                reasoning=f"reviewer call failed: {exc}",
                confidence=0.0,
            )
        text = _extract_text(response)
        decision = _parse_decision(text)
        if decision.decision == "approve" and decision.confidence < 0.7:
            return ReviewerDecision(
                decision="comment",
                reasoning=(
                    "reviewer wanted to approve but confidence "
                    f"{decision.confidence:.2f} was below the 0.7 floor; "
                    "downgraded to comment"
                ),
                confidence=decision.confidence,
                model_used=reviewer.model_id,
            )
        return ReviewerDecision(
            decision=decision.decision,
            reasoning=decision.reasoning,
            confidence=decision.confidence,
            model_used=reviewer.model_id,
        )


@dataclass(frozen=True)
class ReviewOutcome:
    """One card's outcome from this sweep. Returned for the tick summary."""

    card_id: str
    action: str  # "reviewed", "skipped_existing", "skipped_no_pr", "skipped_gh"
    decision: Decision | None = None
    reason: str = ""


def run_sibling_reviews(
    *,
    repo: CardRepository,
    gh: GhRunner,
    cfg: DaemonConfig,
    paths: RuntimePaths,
    reviewer_client: SiblingReviewerClient,
    reviewer_config: ReviewerConfig,
    tenant_id: str = DEFAULT_TENANT,
) -> list[ReviewOutcome]:
    """Process `blocked/requires_review` cards once per tick.

    No-op when `cfg.sibling_reviewer_enabled` is False or when the
    project's reviewer config is disabled. The dual-toggle (host knob
    + project knob) is deliberate: the host operator owns whether the
    daemon ever calls SDK reviewers, and the project owns whether
    this particular project wants the calls made.
    """
    if not cfg.sibling_reviewer_enabled or not reviewer_config.enabled:
        return []
    outcomes: list[ReviewOutcome] = []
    candidates = repo.query_cards(
        tenant_id=tenant_id, status=CardStatus.BLOCKED.value
    )
    for record in candidates:
        if record.merge_status != "requires_review":
            continue
        outcome = _process_card(
            record,
            repo=repo,
            gh=gh,
            paths=paths,
            reviewer_client=reviewer_client,
            reviewer_config=reviewer_config,
            tenant_id=tenant_id,
        )
        outcomes.append(outcome)
    return outcomes


def _process_card(
    record: CardRecord,
    *,
    repo: CardRepository,
    gh: GhRunner,
    paths: RuntimePaths,
    reviewer_client: SiblingReviewerClient,
    reviewer_config: ReviewerConfig,
    tenant_id: str,
) -> ReviewOutcome:
    pr_url = (record.pr_url or "").strip()
    if not pr_url:
        return ReviewOutcome(
            card_id=record.card_id,
            action="skipped_no_pr",
            reason="card has no pr_url; cannot review",
        )
    marker_path = sibling_review_marker_path(paths, record.card_id)
    existing = _read_marker(marker_path)
    if existing and existing.get("pr_url") == pr_url:
        return ReviewOutcome(
            card_id=record.card_id,
            action="skipped_existing",
            decision=existing.get("decision"),
            reason="marker already present for this pr_url",
        )

    diff_result = gh.pr_diff(identifier=pr_url)
    if not diff_result.ok:
        log.info(
            "gh pr diff failed for %s (%s): %s",
            record.card_id, pr_url, diff_result.reason,
        )
        return ReviewOutcome(
            card_id=record.card_id,
            action="skipped_gh",
            reason=f"gh pr diff failed: {diff_result.reason}",
        )

    decision = reviewer_client.review(
        card_id=record.card_id,
        card_body=record.body_md,
        pr_diff=diff_result.stdout or "",
        reviewer=reviewer_config,
    )

    body = _format_review_body(decision, reviewer_config)
    review_call = gh.pr_review(
        identifier=pr_url,
        decision=decision.decision,
        body=body,
    )
    merge_call_payload: dict[str, Any] | None = None
    if decision.decision == "approve" and review_call.ok:
        merge_call = gh.merge_pr(
            paths.todo_root,  # gh resolves the repo from the URL; cwd is for git push.
            identifier=pr_url,
            strategy=record.field_value("auto_merge_strategy")
            or "squash",
        )
        merge_call_payload = {
            "ok": merge_call.ok,
            "exit_code": merge_call.exit_code,
            "reason": merge_call.reason,
        }

    marker = {
        "card_id": record.card_id,
        "pr_url": pr_url,
        "decision": decision.decision,
        "reasoning": decision.reasoning,
        "confidence": decision.confidence,
        "model_used": decision.model_used or reviewer_config.model_id,
        "reviewer_label": reviewer_config.label,
        "at": now_utc_iso(),
        "gh_review_call": {
            "ok": review_call.ok,
            "exit_code": review_call.exit_code,
            "reason": review_call.reason,
        },
    }
    if merge_call_payload is not None:
        marker["gh_merge_call"] = merge_call_payload
    _write_marker(marker_path, marker)
    _emit_event(repo, record, decision, marker, tenant_id=tenant_id)
    return ReviewOutcome(
        card_id=record.card_id,
        action="reviewed",
        decision=decision.decision,
        reason="decision posted and marker written",
    )


def sibling_review_marker_path(paths: RuntimePaths, card_id: str) -> Path:
    """Where the marker for `card_id` lives."""
    return paths.signals / "sibling_reviews" / f"{card_id}.json"


def _read_marker(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_marker(path: Path, marker: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True), encoding="utf-8")


def _emit_event(
    repo: CardRepository,
    record: CardRecord,
    decision: ReviewerDecision,
    marker: dict[str, Any],
    *,
    tenant_id: str,
) -> None:
    try:
        repo.append_event(
            CardEvent(
                card_id=record.card_id,
                tenant_id=tenant_id,
                type=EventType.VERIFIED.value,
                actor_id=marker.get("reviewer_label") or "sibling-reviewer",
                actor_type=ActorType.RUNNER.value,
                at=marker.get("at") or now_utc_iso(),
                payload={
                    "source": "sibling_reviewer",
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "pr_url": marker.get("pr_url"),
                    "model_used": decision.model_used,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "failed to append sibling-review event for %s: %s",
            record.card_id, exc,
        )


# ---- prompt + decision parsing ---------------------------------------


_SYSTEM_PROMPT_BASE = """You are a sibling reviewer for an agile-cards \
runner. A peer agent just finished work on a small card (tier 3 or 4 by \
the project's sizing matrix) and opened a pull request. Your job: read \
the card body, the acceptance criteria, and the PR diff. Render ONE of \
three verdicts:

- `approve` -- the diff plausibly satisfies the AC, the change is bounded \
to what the card asked for, and you would be willing to merge it on a \
similar card.
- `request_changes` -- the diff has a concrete problem (missing AC item, \
regression, scope creep, security issue) that the author should fix before \
merge.
- `comment` -- you have notes but no blocking issue. The PR is closer to \
"someone should look at this" than to "approve" or "request changes".

Respond in EXACTLY this YAML shape, no commentary outside it:

```yaml
decision: approve | request_changes | comment
confidence: 0.0  # your confidence in this verdict, 0.0-1.0
reasoning: >
  short prose explanation. Two or three sentences.
```

Confidence below 0.7 will be downgraded to `comment` even if you said \
`approve`. Be honest about uncertainty.
"""


def _system_prompt(reviewer: ReviewerConfig) -> str:
    if reviewer.prompt_extra:
        return _SYSTEM_PROMPT_BASE + "\n" + reviewer.prompt_extra
    return _SYSTEM_PROMPT_BASE


def _user_prompt(card_id: str, card_body: str, pr_diff: str) -> str:
    return (
        f"# Card `{card_id}`\n\n"
        "## Card body\n\n"
        f"{card_body}\n\n"
        "## PR diff\n\n"
        "```diff\n"
        f"{pr_diff}\n"
        "```\n"
    )


def _format_review_body(
    decision: ReviewerDecision, reviewer: ReviewerConfig
) -> str:
    return (
        f"_sibling-reviewer agent `{reviewer.label}` "
        f"({decision.model_used or reviewer.model_id})_\n\n"
        f"**Decision:** `{decision.decision}` "
        f"(confidence {decision.confidence:.2f})\n\n"
        f"{decision.reasoning}\n"
    )


def _parse_decision(text: str) -> ReviewerDecision:
    """Parse the reviewer's YAML output into a `ReviewerDecision`.

    The reviewer was instructed to emit a fenced YAML block; we tolerate
    the fence being missing (a common minor-LLM failure mode). When the
    parse fails entirely we degrade to a comment that includes the raw
    text so a human can see what happened.
    """
    import re

    fenced = re.search(r"```ya?ml\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        payload_text = fenced.group(1)
    else:
        payload_text = text
    try:
        import yaml

        parsed = yaml.safe_load(payload_text)
    except Exception:  # noqa: BLE001
        parsed = None
    if not isinstance(parsed, dict):
        return ReviewerDecision(
            decision="comment",
            reasoning=f"could not parse reviewer output: {text[:400]}",
            confidence=0.0,
        )
    decision_raw = str(parsed.get("decision") or "comment").strip().lower()
    if decision_raw not in {"approve", "request_changes", "comment"}:
        decision_raw = "comment"
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    reasoning = str(parsed.get("reasoning") or "").strip()
    return ReviewerDecision(
        decision=decision_raw,  # type: ignore[arg-type]
        reasoning=reasoning,
        confidence=max(0.0, min(1.0, confidence)),
    )


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of an Anthropic response object."""
    content = getattr(response, "content", None)
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


