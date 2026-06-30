"""Tests for the command handler.

These tests intentionally use cross-platform commands so the suite
runs on both POSIX and Windows. We use `sys.executable` to run a
short Python one-liner rather than shell builtins because Python is
the only thing we can rely on existing in PATH.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from verifier.handlers import command
from verifier.project_config import ProjectConfig


PY = sys.executable


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)
        self.cfg = ProjectConfig()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pass_on_zero_exit(self):
        r = command.run(
            {
                "description": "x",
                "type": "command",
                "command": [PY, "-c", "import sys; sys.exit(0)"],
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed, r.evidence)

    def test_fail_on_nonzero_exit(self):
        r = command.run(
            {
                "description": "x",
                "type": "command",
                "command": [PY, "-c", "import sys; sys.exit(2)"],
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)
        self.assertEqual(r.evidence["exit_code"], 2)

    def test_expected_exit_code_override(self):
        r = command.run(
            {
                "description": "x",
                "type": "command",
                "command": [PY, "-c", "import sys; sys.exit(7)"],
                "expected_exit_code": 7,
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)

    def test_timeout_kills_process(self):
        r = command.run(
            {
                "description": "x",
                "type": "command",
                "command": [PY, "-c", "import time; time.sleep(10)"],
                "timeout_sec": 1,
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)
        self.assertTrue(r.evidence["timed_out"])

    def test_env_is_scrubbed_by_default(self):
        # Set an obvious "credential" pattern in the parent env. The
        # handler MUST NOT forward it.
        os.environ["FAKE_API_KEY"] = "should-not-be-visible"
        try:
            r = command.run(
                {
                    "description": "x",
                    "type": "command",
                    "command": [
                        PY,
                        "-c",
                        "import os, sys; sys.stdout.write(os.environ.get('FAKE_API_KEY','<absent>'))",
                    ],
                },
                worktree=self.worktree,
                project_cfg=self.cfg,
            )
        finally:
            del os.environ["FAKE_API_KEY"]
        self.assertTrue(r.passed, r.evidence)
        self.assertIn("<absent>", r.evidence["stdout"])

    def test_per_item_env_override_is_honored(self):
        r = command.run(
            {
                "description": "x",
                "type": "command",
                "command": [
                    PY,
                    "-c",
                    "import os, sys; sys.stdout.write(os.environ.get('MY_VAR','<absent>'))",
                ],
                "env": {"MY_VAR": "hello"},
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertIn("hello", r.evidence["stdout"])

    def test_string_command_splits_via_shlex(self):
        r = command.run(
            {
                "description": "x",
                "type": "command",
                "command": f'"{PY}" -c "print(1)"',
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed, r.evidence)

    def test_cwd_per_item_override(self):
        sub = self.worktree / "sub"
        sub.mkdir()
        r = command.run(
            {
                "description": "x",
                "type": "command",
                "command": [PY, "-c", "import os, sys; sys.stdout.write(os.getcwd())"],
                "cwd": "sub",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertIn("sub", r.evidence["stdout"])


if __name__ == "__main__":
    unittest.main()
