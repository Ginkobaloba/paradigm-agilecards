"""Tests for the filesystem handlers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from verifier.handlers import (
    file_absent,
    file_absent_content,
    file_contains,
    file_exists,
)
from verifier.project_config import ProjectConfig


class _FsBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name)
        self.cfg = ProjectConfig()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str, content: str) -> Path:
        p = self.worktree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p


class FileExistsTests(_FsBase):
    def test_pass_on_existing_file(self):
        self._write("src/x.py", "print('x')")
        r = file_exists.run(
            {"description": "x", "type": "file_exists", "path": "src/x.py"},
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)

    def test_fail_on_missing(self):
        r = file_exists.run(
            {"description": "x", "type": "file_exists", "path": "src/nope.py"},
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)


class FileAbsentTests(_FsBase):
    def test_pass_on_missing(self):
        r = file_absent.run(
            {"description": "x", "type": "file_absent", "path": "deleted.py"},
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)

    def test_fail_when_present(self):
        self._write("present.py", "x")
        r = file_absent.run(
            {"description": "x", "type": "file_absent", "path": "present.py"},
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)


class FileContainsTests(_FsBase):
    def test_pass_on_literal_match(self):
        self._write("a.txt", "hello world")
        r = file_contains.run(
            {
                "description": "x",
                "type": "file_contains",
                "path": "a.txt",
                "literal": "hello",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)

    def test_fail_on_literal_miss(self):
        self._write("a.txt", "hello world")
        r = file_contains.run(
            {
                "description": "x",
                "type": "file_contains",
                "path": "a.txt",
                "literal": "goodbye",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)

    def test_regex_case_insensitive(self):
        self._write("a.txt", "Hello World")
        r = file_contains.run(
            {
                "description": "x",
                "type": "file_contains",
                "path": "a.txt",
                "pattern": r"hello",
                "case_sensitive": False,
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)

    def test_missing_file_fails_with_error_evidence(self):
        r = file_contains.run(
            {
                "description": "x",
                "type": "file_contains",
                "path": "nope.txt",
                "literal": "x",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)
        self.assertIn("does not exist", r.evidence["error"])


class FileAbsentContentTests(_FsBase):
    def test_pass_when_pattern_absent(self):
        self._write("a.txt", "no secrets here")
        r = file_absent_content.run(
            {
                "description": "x",
                "type": "file_absent_content",
                "path": "a.txt",
                "pattern": r"api_key\s*=\s*['\"][^'\"]+['\"]",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)

    def test_fail_when_pattern_present(self):
        self._write("a.txt", "api_key='hunter2'")
        r = file_absent_content.run(
            {
                "description": "x",
                "type": "file_absent_content",
                "path": "a.txt",
                "pattern": r"api_key\s*=\s*['\"][^'\"]+['\"]",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)

    def test_missing_file_passes(self):
        r = file_absent_content.run(
            {
                "description": "x",
                "type": "file_absent_content",
                "path": "nope.txt",
                "literal": "anything",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed)


if __name__ == "__main__":
    unittest.main()
