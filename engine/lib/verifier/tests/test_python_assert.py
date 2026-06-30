"""Tests for the python_assert handler.

Covers the happy path, the disallowed-namespace path, and the
write-mode `open` block.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from verifier.handlers import python_assert
from verifier.project_config import ProjectConfig


class PythonAssertTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)
        self.cfg = ProjectConfig()
        (self.worktree / "hello.txt").write_text("Retry-After: 5", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_truthy_pass(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": "1 + 1 == 2",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)

    def test_falsy_fail(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": "1 == 2",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)

    def test_os_path_exists_works(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": "os.path.exists(str(worktree / 'hello.txt'))",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed, r.evidence)

    def test_disallowed_import_blocked(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": "__import__('subprocess')",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)
        self.assertIn("not allowed", r.evidence["error"])

    def test_write_open_blocked(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": "open('out.txt', 'w')",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)
        self.assertIn("write mode", r.evidence["error"])

    def test_read_open_works(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": (
                    "'Retry-After' in open(str(worktree / 'hello.txt')).read()"
                ),
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed, r.evidence)

    def test_os_system_blocked(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": "os.system('ls')",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)
        self.assertIn("os.system", r.evidence["error"])

    def test_syntax_error_fails_gracefully(self):
        r = python_assert.run(
            {
                "description": "x",
                "type": "python_assert",
                "expression": "1 + + )(",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)
        self.assertIn("SyntaxError", r.evidence["error"])


if __name__ == "__main__":
    unittest.main()
