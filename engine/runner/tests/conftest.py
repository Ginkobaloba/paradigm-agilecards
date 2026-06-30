"""Shared fixtures for the runner test suite.

After the chunk 2b cutover the card store is canonical. `card_factory`
inserts a `CardRecord` into a SQLite store rather than dropping a
Markdown file into `backlog/`; the daemon claims, projects, and runs
cards out of that store.
"""
from __future__ import annotations

import sys
import textwrap
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# Make `src/cards_runner` importable without an install. We do this
# here so `pip install -e .` is not required to run the suite from a
# fresh clone; the README still documents `pip install -e .[dev]` as
# the supported path.
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from cards_runner.common.types import (  # noqa: E402
    DaemonConfig, RuntimePaths,
)
from cards_runner.store.projection import card_text_to_record  # noqa: E402
from cards_runner.store.sqlite_store import SqliteRepository  # noqa: E402


def _make_card_text(
    card_id: str,
    *,
    status: str = "backlog",
    trace_id: str | None = None,
) -> str:
    trace = trace_id or str(uuid.uuid4())
    return textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Test card {card_id}
        project: /tmp/test-project
        status: {status}
        points: 2
        stakes: low
        difficulty: shallow
        thinking_depth: shallow
        model: claude-haiku-4-5-20251001
        extended_thinking: false
        model_floor: haiku
        pin_required: false
        requires_pre_approval: false
        cost_cap_usd: null
        estimated_tokens: 0
        actual_tokens: null
        estimated_duration_minutes: 0
        actual_duration_minutes: null
        trace_id: {trace}
        sizing_note: "test card"
        depends_on: []
        touches: []
        batch: bTST
        story_hash: deadbeef
        created: 2026-05-19
        started_at: null
        finished_at: null
        claimed_by: null
        model_used: null
        last_heartbeat: null
        branch: card/{card_id}
        base_branch: main
        merge_status: pending
        verified_at: null
        verified_by: null
        verifier_skipped_reason: null
        cascade_history: []
        verifier_cascade_history: []
        standup_reason: null
        ---

        ## Context

        Test card.

        ## Scope

        - nothing.

        ## Out of scope

        - real work.

        ## Acceptance criteria

        ```yaml
        acceptance_criteria:
          - description: "Smoke"
            type: file_exists
            path: "README.md"
        ```

        ## Pointers

        - none.
        """
    )


@pytest.fixture
def todo_root(tmp_path: Path) -> Path:
    root = tmp_path / "todo"
    paths = RuntimePaths.from_root(root)
    paths.ensure()
    return root


@pytest.fixture
def paths(todo_root: Path) -> RuntimePaths:
    return RuntimePaths.from_root(todo_root)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    """Path to the per-test SQLite card store file."""
    return tmp_path / "cards.db"


@pytest.fixture
def store_spec(store_path: Path) -> str:
    return f"sqlite:{store_path}"


@pytest.fixture
def repo(store_path: Path) -> Iterator[SqliteRepository]:
    """A schema-initialized SQLite card store for the test thread.

    The integration test runs the daemon in its own thread, where the
    daemon opens its own connection to the same file; SQLite WAL makes
    that safe. Tests that drive the daemon synchronously inject this
    repo so there is one connection on one thread.
    """
    store = SqliteRepository.open(str(store_path))
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def daemon_cfg(todo_root: Path, store_spec: str) -> DaemonConfig:
    """Daemon config for chunks 1-2 tests.

    Verifier is disabled by default here so the chunk 2b-ii exit-
    routing tests keep their semantics (a clean rc=0 leaves the card
    active). Chunk 3 verifier tests use their own fixture that flips
    `verifier_enabled=True`.
    """
    return DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        poll_interval_sec=0.1,
        max_parallel=4,
        max_parallel_pinned=1,
        orphan_timeout_minutes=120,
        heartbeat_interval_sec=0.5,
        stub_sleep_sec=0.5,
        worktree_forensic_ttl_hours=24,
        skip_worktree=True,
        verifier_enabled=False,
    )


@pytest.fixture
def daemon_cfg_verifier(todo_root: Path, store_spec: str) -> DaemonConfig:
    """Chunk 3 fixture: daemon config with the verifier active.

    Subjective cascade is disabled so verifier tests run token-free by
    default; the per-test that wants to exercise the cascade injects a
    fake client directly via `verify_card(...)` instead of relying on
    the daemon's `_build_subjective_client`.
    """
    return DaemonConfig(
        todo_root=todo_root,
        store_spec=store_spec,
        poll_interval_sec=0.1,
        max_parallel=4,
        max_parallel_pinned=1,
        orphan_timeout_minutes=120,
        heartbeat_interval_sec=0.5,
        stub_sleep_sec=0.5,
        worktree_forensic_ttl_hours=24,
        skip_worktree=True,
        verifier_enabled=True,
        verifier_cascade_disabled=True,  # subjective items -> standup, no LLM call.
    )


@pytest.fixture
def card_factory(repo: SqliteRepository) -> Any:
    """Insert a card into the store and return its id.

    Cards are created `backlog` by default. Tests that need an
    `active` card claim it with `repo.claim_card(...)`, which is the
    real path and stamps the claim fields the orphan logic reads.
    """

    def make(card_id: str = "bTST-01-test", *, status: str = "backlog") -> str:
        text = _make_card_text(card_id, status=status)
        record = card_text_to_record(
            text, card_id_fallback=card_id, status_override=status
        )
        repo.create_card(record)
        return card_id

    return make
