"""End-to-end test: mixed deterministic + subjective card."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from verifier.project_config import ProjectConfig
from verifier.runner import verify_card


class _Evaluator:
    """Mock evaluator that returns scripted responses per tier."""

    def __init__(self, per_tier: dict[str, list[dict[str, object]]]) -> None:
        self.per_tier = per_tier
        self.calls: list[str] = []

    def __call__(self, tier, model, card_body, items, evidence_json):
        self.calls.append(tier)
        entries = self.per_tier.get(tier, [])
        out = []
        for i, _ in enumerate(items):
            out.append(
                entries[i]
                if i < len(entries)
                else {"result": "fail", "confidence": 0.0}
            )
        return {"items": out}


class EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)
        (self.worktree / "src").mkdir()
        (self.worktree / "src" / "rate_limit.ts").write_text(
            "Retry-After header set here", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_pass(self):
        ev = _Evaluator(
            {"haiku": [{"result": "pass", "confidence": 0.95, "reasoning": "clean"}]}
        )
        items = [
            {
                "description": "file exists",
                "type": "file_exists",
                "path": "src/rate_limit.ts",
            },
            {
                "description": "Retry-After in source",
                "type": "file_contains",
                "path": "src/rate_limit.ts",
                "literal": "Retry-After",
            },
            {
                "description": "no api key leaked",
                "type": "file_absent_content",
                "path": "src/rate_limit.ts",
                "pattern": r"api_key\s*=",
            },
            {
                "description": "echo works",
                "type": "command",
                "command": [sys.executable, "-c", "print('ok')"],
            },
            {
                "description": "header present per python",
                "type": "python_assert",
                "expression": (
                    "'Retry-After' in open(str(worktree / 'src' / 'rate_limit.ts')).read()"
                ),
            },
            {
                "description": "error voice is on-brand",
                "type": "subjective",
                "evidence_required": "paste a rendered error",
            },
        ]
        result = verify_card(
            ac_items=items,
            card_body="card body here",
            subjective_evidence={"index_5": "Rendered: Retry-After: 60"},
            worktree=self.worktree,
            project_cfg=ProjectConfig(),
            card_points=5,  # subjective requires tier 5+
            call_evaluator=ev,
        )
        self.assertEqual(result.overall_status, "pass", result.items)
        self.assertEqual(len(result.items), 6)
        for ir in result.items:
            self.assertTrue(ir.passed, ir.handler_result.evidence)

    def test_subjective_exhaustion_routes_to_standup(self):
        ev = _Evaluator(
            {
                "haiku": [{"result": "fail", "confidence": 0.2}],
                "sonnet": [{"result": "fail", "confidence": 0.4}],
                "opus": [{"result": "fail", "confidence": 0.7}],
            }
        )
        items = [
            {
                "description": "file exists",
                "type": "file_exists",
                "path": "src/rate_limit.ts",
            },
            {
                "description": "error voice is on-brand",
                "type": "subjective",
                "evidence_required": "paste a rendered error",
            },
        ]
        result = verify_card(
            ac_items=items,
            card_body="card body",
            subjective_evidence={"index_1": "ambiguous evidence"},
            worktree=self.worktree,
            project_cfg=ProjectConfig(),
            card_points=5,
            call_evaluator=ev,
        )
        self.assertEqual(result.overall_status, "needs_standup_review")
        self.assertEqual(result.standup_reason_items, (1,))
        # Cascade history appendix should contain three entries for item 1.
        item1_entries = [
            e for e in result.cascade_history_appendix if e["item_idx"] == 1
        ]
        self.assertEqual(len(item1_entries), 3)
        self.assertEqual(
            [e["tier_attempted"] for e in item1_entries],
            ["haiku", "sonnet", "opus"],
        )

    def test_schema_error_surfaces_without_running_handlers(self):
        ev = _Evaluator({"haiku": [{"result": "pass", "confidence": 1.0}]})
        items = [
            {
                "description": "missing type",
                "path": "src/x.py",
            },
        ]
        result = verify_card(
            ac_items=items,
            card_body="",
            subjective_evidence={},
            worktree=self.worktree,
            project_cfg=ProjectConfig(),
            call_evaluator=ev,
        )
        self.assertEqual(result.overall_status, "fail")
        self.assertEqual(ev.calls, [])  # subjective handler never called

    def test_results_returned_in_declaration_order(self):
        items = [
            {"description": "A", "type": "file_exists", "path": "src/rate_limit.ts"},
            {"description": "B", "type": "file_absent", "path": "src/__nope__"},
            {"description": "C", "type": "file_exists", "path": "src/rate_limit.ts"},
        ]
        result = verify_card(
            ac_items=items,
            card_body="",
            worktree=self.worktree,
            project_cfg=ProjectConfig(),
        )
        self.assertEqual(
            [r.item["description"] for r in result.items],
            ["A", "B", "C"],
        )


if __name__ == "__main__":
    unittest.main()
