"""Tests for the v1.3 verifier library.

Layout: one test module per handler, plus a schema test module, a
cascade test module, and an end-to-end module that runs a mixed
deterministic + subjective card through the orchestrator.

Run via `python -m unittest discover lib/verifier/tests` from the
repo root. The tests do NOT require pytest. They do not hit the
network: subjective tests inject a mock evaluator; HTTP tests run
against `127.0.0.1` via `http.server`.
"""
