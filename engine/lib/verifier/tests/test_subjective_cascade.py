"""Tests for the subjective handler cascade.

The cascade is the most novel piece of v1.3 and the one the tests
should be most paranoid about. We inject a mock evaluator that
returns scripted (result, confidence) pairs per tier so we can
exercise:

- haiku above threshold -> pass at haiku tier
- haiku low conf -> sonnet above threshold -> pass at sonnet tier
- haiku and sonnet low conf -> opus above threshold -> pass at opus
- all three tiers low conf -> needs_standup_review
- mixed: two subjective items, one settles at haiku, the other
  needs to escalate (cascade lookups continue only on the unsettled
  item)
- cascade disabled -> needs_standup_review immediately
"""
from __future__ import annotations

import unittest
from pathlib import Path

from verifier.handlers.subjective import (
    SubjectiveOutcome,
    evaluate_subjective_batch,
)
from verifier.project_config import ProjectConfig


class _ScriptedEvaluator:
    """Returns scripted responses indexed by tier name.

    `script[tier]` is a list with one entry per pending item AT THAT
    TIER. The evaluator does not need to track which item index the
    pending list maps to; the orchestrator does.
    """

    def __init__(self, script: dict[str, list[dict[str, object]]]) -> None:
        self.script = script
        self.calls: list[tuple[str, int]] = []

    def __call__(
        self,
        tier: str,
        model: str,
        card_body: str,
        items: list[dict[str, object]],
        evidence_json: str,
    ) -> dict[str, object]:
        self.calls.append((tier, len(items)))
        entries = self.script.get(tier, [])
        # Echo back exactly len(items) entries; truncate or pad with
        # a low-conf fail (which mimics a malformed response and is
        # what the orchestrator should escalate on).
        out: list[dict[str, object]] = []
        for i in range(len(items)):
            if i < len(entries):
                out.append(entries[i])
            else:
                out.append(
                    {"result": "fail", "confidence": 0.0, "reasoning": "scripted-padding"}
                )
        return {"items": out}


class CascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ProjectConfig()  # default threshold 0.85, haiku -> opus

    def _item(self, idx: int) -> dict[str, object]:
        return {
            "description": f"item {idx}",
            "type": "subjective",
            "evidence_required": "evidence",
        }

    def _evidence(self, n: int) -> dict[str, str]:
        return {f"index_{i}": f"evidence-{i}" for i in range(n)}

    def test_pass_at_haiku_above_threshold(self):
        ev = _ScriptedEvaluator(
            {"haiku": [{"result": "pass", "confidence": 0.95, "reasoning": "easy"}]}
        )
        outcomes = evaluate_subjective_batch(
            items=[self._item(0)],
            card_body="",
            evidence=self._evidence(1),
            project_cfg=self.cfg,
            call_evaluator=ev,
        )
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].verdict, "pass")
        self.assertEqual(len(ev.calls), 1)
        self.assertEqual(ev.calls[0][0], "haiku")

    def test_escalates_haiku_to_sonnet(self):
        ev = _ScriptedEvaluator(
            {
                "haiku": [{"result": "fail", "confidence": 0.4, "reasoning": "unsure"}],
                "sonnet": [{"result": "pass", "confidence": 0.9, "reasoning": "yes"}],
            }
        )
        outcomes = evaluate_subjective_batch(
            items=[self._item(0)],
            card_body="",
            evidence=self._evidence(1),
            project_cfg=self.cfg,
            call_evaluator=ev,
        )
        self.assertEqual(outcomes[0].verdict, "pass")
        self.assertEqual(len(outcomes[0].cascade_history), 2)
        self.assertEqual(outcomes[0].cascade_history[0].tier_attempted, "haiku")
        self.assertEqual(outcomes[0].cascade_history[1].tier_attempted, "sonnet")

    def test_escalates_through_opus_to_success(self):
        ev = _ScriptedEvaluator(
            {
                "haiku": [{"result": "fail", "confidence": 0.3, "reasoning": "no"}],
                "sonnet": [{"result": "fail", "confidence": 0.6, "reasoning": "still unsure"}],
                "opus": [{"result": "pass", "confidence": 0.95, "reasoning": "yes"}],
            }
        )
        outcomes = evaluate_subjective_batch(
            items=[self._item(0)],
            card_body="",
            evidence=self._evidence(1),
            project_cfg=self.cfg,
            call_evaluator=ev,
        )
        self.assertEqual(outcomes[0].verdict, "pass")
        self.assertEqual(
            [a.tier_attempted for a in outcomes[0].cascade_history],
            ["haiku", "sonnet", "opus"],
        )

    def test_cascade_exhaustion_routes_to_standup(self):
        ev = _ScriptedEvaluator(
            {
                "haiku": [{"result": "fail", "confidence": 0.2, "reasoning": "no"}],
                "sonnet": [{"result": "fail", "confidence": 0.5, "reasoning": "no"}],
                "opus": [{"result": "fail", "confidence": 0.7, "reasoning": "no"}],
            }
        )
        outcomes = evaluate_subjective_batch(
            items=[self._item(0)],
            card_body="",
            evidence=self._evidence(1),
            project_cfg=self.cfg,
            call_evaluator=ev,
        )
        self.assertEqual(outcomes[0].verdict, "needs_standup_review")
        self.assertEqual(len(outcomes[0].cascade_history), 3)

    def test_mixed_one_settles_other_escalates(self):
        ev = _ScriptedEvaluator(
            {
                "haiku": [
                    {"result": "pass", "confidence": 0.95, "reasoning": "easy"},
                    {"result": "fail", "confidence": 0.3, "reasoning": "unsure"},
                ],
                "sonnet": [
                    {"result": "pass", "confidence": 0.9, "reasoning": "yes"},
                ],
            }
        )
        outcomes = evaluate_subjective_batch(
            items=[self._item(0), self._item(1)],
            card_body="",
            evidence=self._evidence(2),
            project_cfg=self.cfg,
            call_evaluator=ev,
        )
        self.assertEqual(outcomes[0].verdict, "pass")
        self.assertEqual(outcomes[1].verdict, "pass")
        # Haiku was called with 2 items; sonnet with only the 1 pending.
        self.assertEqual(ev.calls, [("haiku", 2), ("sonnet", 1)])

    def test_high_confidence_fail_settles_as_fail(self):
        ev = _ScriptedEvaluator(
            {"haiku": [{"result": "fail", "confidence": 0.92, "reasoning": "no"}]}
        )
        outcomes = evaluate_subjective_batch(
            items=[self._item(0)],
            card_body="",
            evidence=self._evidence(1),
            project_cfg=self.cfg,
            call_evaluator=ev,
        )
        self.assertEqual(outcomes[0].verdict, "fail")
        self.assertEqual(len(outcomes[0].cascade_history), 1)

    def test_cascade_disabled_goes_straight_to_standup(self):
        cfg = ProjectConfig(subjective_cascade_disabled=True)
        ev = _ScriptedEvaluator({"haiku": [{"result": "pass", "confidence": 1.0}]})
        outcomes = evaluate_subjective_batch(
            items=[self._item(0)],
            card_body="",
            evidence=self._evidence(1),
            project_cfg=cfg,
            call_evaluator=ev,
        )
        self.assertEqual(outcomes[0].verdict, "needs_standup_review")
        self.assertEqual(ev.calls, [])


if __name__ == "__main__":
    unittest.main()
