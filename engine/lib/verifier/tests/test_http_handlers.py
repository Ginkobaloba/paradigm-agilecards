"""Tests for the HTTP handlers.

Uses Python's built-in `http.server` on a random port. No network
traffic to the wider internet; we bind to 127.0.0.1.
"""
from __future__ import annotations

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from verifier.handlers import http_contains, http_status
from verifier.project_config import ProjectConfig


class _Handler(BaseHTTPRequestHandler):
    """Tiny in-process server returning a deterministic body."""

    routes: dict[str, tuple[int, bytes]] = {
        "/ok": (200, b"hello world from test server"),
        "/404": (404, b"not found"),
        "/json": (200, b'{"status":"ok"}'),
    }

    def do_GET(self) -> None:  # noqa: N802 - inherited interface
        status, body = self.routes.get(self.path, (500, b"unknown"))
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object, **_kwargs: object) -> None:
        return  # silence test output


def _start_server() -> tuple[HTTPServer, threading.Thread, int]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


class HttpStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.thread, cls.port = _start_server()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        self.cfg = ProjectConfig(network_checks_allowed=True)
        self.worktree = Path(".")

    def test_pass_on_expected_status(self):
        r = http_status.run(
            {
                "description": "x",
                "type": "http_status",
                "url": f"{self.base}/ok",
                "expected_status": 200,
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed, r.evidence)

    def test_fail_on_wrong_status(self):
        r = http_status.run(
            {
                "description": "x",
                "type": "http_status",
                "url": f"{self.base}/404",
                "expected_status": 200,
                "retries": 0,
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)

    def test_list_of_acceptable_statuses(self):
        r = http_status.run(
            {
                "description": "x",
                "type": "http_status",
                "url": f"{self.base}/404",
                "expected_status": [200, 404],
                "retries": 0,
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed, r.evidence)

    def test_network_gate_fails_when_disabled(self):
        cfg = ProjectConfig(network_checks_allowed=False)
        r = http_status.run(
            {
                "description": "x",
                "type": "http_status",
                "url": f"{self.base}/ok",
                "expected_status": 200,
            },
            worktree=self.worktree,
            project_cfg=cfg,
        )
        self.assertFalse(r.passed)
        self.assertIn("network_checks_allowed", r.evidence["error"])


class HttpContainsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.thread, cls.port = _start_server()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        self.cfg = ProjectConfig(network_checks_allowed=True)
        self.worktree = Path(".")

    def test_literal_match(self):
        r = http_contains.run(
            {
                "description": "x",
                "type": "http_contains",
                "url": f"{self.base}/ok",
                "literal": "hello world",
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertTrue(r.passed, r.evidence)

    def test_pattern_miss_fails(self):
        r = http_contains.run(
            {
                "description": "x",
                "type": "http_contains",
                "url": f"{self.base}/ok",
                "pattern": r"^goodbye$",
                "retries": 0,
            },
            worktree=self.worktree,
            project_cfg=self.cfg,
        )
        self.assertFalse(r.passed)


if __name__ == "__main__":
    unittest.main()
