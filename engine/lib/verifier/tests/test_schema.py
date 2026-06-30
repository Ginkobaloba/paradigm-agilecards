"""Schema validation tests."""
from __future__ import annotations

import unittest

from verifier.schema import validate_ac_items


class SchemaTests(unittest.TestCase):
    def test_valid_minimal_card_passes(self):
        items = [
            {"description": "file exists", "type": "file_exists", "path": "src/x.py"},
        ]
        report = validate_ac_items(items)
        self.assertTrue(report.ok, report.issues)

    def test_missing_type_fails(self):
        report = validate_ac_items(
            [{"description": "x", "path": "src/x.py"}]
        )
        self.assertFalse(report.ok)
        self.assertIn("missing required field 'type'", report.issues[0].message)

    def test_unknown_type_fails(self):
        report = validate_ac_items(
            [{"description": "x", "type": "bogus", "path": "p"}]
        )
        self.assertFalse(report.ok)
        self.assertIn("not a recognized canonical type", report.issues[0].message)

    def test_file_contains_xor_pattern_literal(self):
        # Neither pattern nor literal -> fail
        report = validate_ac_items(
            [{"description": "x", "type": "file_contains", "path": "p"}]
        )
        self.assertFalse(report.ok)
        # Both pattern and literal -> fail
        report = validate_ac_items(
            [
                {
                    "description": "x",
                    "type": "file_contains",
                    "path": "p",
                    "pattern": "a",
                    "literal": "b",
                }
            ]
        )
        self.assertFalse(report.ok)
        # Exactly one -> ok
        report = validate_ac_items(
            [
                {
                    "description": "x",
                    "type": "file_contains",
                    "path": "p",
                    "pattern": "a",
                }
            ]
        )
        self.assertTrue(report.ok, report.issues)

    def test_http_gated_by_network_checks_allowed(self):
        items = [
            {
                "description": "x",
                "type": "http_status",
                "url": "http://x",
                "expected_status": 200,
            }
        ]
        report = validate_ac_items(items, network_checks_allowed=False)
        self.assertFalse(report.ok)
        report = validate_ac_items(items, network_checks_allowed=True)
        self.assertTrue(report.ok, report.issues)

    def test_subjective_requires_tier_5_or_higher(self):
        items = [
            {
                "description": "x",
                "type": "subjective",
                "evidence_required": "paste output",
            }
        ]
        report = validate_ac_items(items, card_points=3)
        self.assertFalse(report.ok)
        report = validate_ac_items(items, card_points=5)
        self.assertTrue(report.ok, report.issues)

    def test_empty_list_fails(self):
        report = validate_ac_items([])
        self.assertFalse(report.ok)

    def test_unknown_field_rejected(self):
        report = validate_ac_items(
            [
                {
                    "description": "x",
                    "type": "file_exists",
                    "path": "p",
                    "wat": True,
                }
            ]
        )
        self.assertFalse(report.ok)
        self.assertIn("unknown field 'wat'", report.issues[0].message)


if __name__ == "__main__":
    unittest.main()
