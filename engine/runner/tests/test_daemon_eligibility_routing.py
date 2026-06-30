"""Daemon-level eligibility routing (chunk 4).

The eligibility logic itself is unit-tested in `test_eligibility.py`.
This file exercises the daemon's response to a `block` outcome: a
story-drifted backlog card must be transitioned to `blocked` so the
runner stops re-reading the source file every tick. Skip outcomes
(missing pre-approval, unmet dependency) are NOT transitioned because
they are by nature transient.
"""
from __future__ import annotations

import hashlib
import textwrap
import uuid
from pathlib import Path

from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.daemon import Daemon
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository


def _seed_card_with_source(
    repo: SqliteRepository,
    card_id: str,
    *,
    story_hash: str,
    story_source_path: Path,
) -> None:
    trace = str(uuid.uuid4())
    text = textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Test card
        project: /tmp/test-project
        status: backlog
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
        depends_on: []
        touches: []
        batch: bTST
        story_hash: {story_hash}
        story_source_path: {story_source_path}
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

        ## Acceptance criteria

        ```yaml
        acceptance_criteria:
          - description: trivial
            type: file_exists
            path: anything
        ```
        """
    )
    record = card_text_to_record(text, card_id_fallback=card_id)
    repo.create_card(record)


def test_story_drift_transitions_card_to_blocked(
    repo: SqliteRepository,
    paths: RuntimePaths,
    daemon_cfg: DaemonConfig,
    tmp_path: Path,
) -> None:
    story = tmp_path / "story.md"
    story.write_text("the story has changed since plan time", encoding="utf-8")
    # Plant a hash that does not match the file's current bytes.
    _seed_card_with_source(
        repo, "bTST-E1-drift",
        story_hash="ffeeddccbbaa00112233445566778899",
        story_source_path=story,
    )

    daemon = Daemon(daemon_cfg, repo=repo)
    record = repo.get_card("bTST-E1-drift")
    assert record is not None
    eligible = daemon._is_eligible(record)
    assert eligible is False

    after = repo.get_card("bTST-E1-drift")
    assert after is not None
    assert after.status == CardStatus.BLOCKED.value
    types = [e.type for e in repo.list_events("bTST-E1-drift")]
    assert "blocked" in types


def test_dependency_unmet_leaves_card_in_backlog(
    repo: SqliteRepository,
    paths: RuntimePaths,
    daemon_cfg: DaemonConfig,
    tmp_path: Path,
) -> None:
    # A skipped card (unmet dep) must NOT be transitioned out of
    # backlog -- the next tick may find the dep merged.
    story = tmp_path / "story.md"
    story.write_text("matches", encoding="utf-8")
    sha = hashlib.sha256(b"matches").hexdigest()
    _seed_card_with_source(
        repo, "bTST-E2-parent",
        story_hash=sha, story_source_path=story,
    )
    _seed_card_with_source(
        repo, "bTST-E2-child",
        story_hash=sha, story_source_path=story,
    )
    # Wire the dep into the dependencies table.
    repo.add_dependency("bTST-E2-child", "bTST-E2-parent")

    daemon = Daemon(daemon_cfg, repo=repo)
    record = repo.get_card("bTST-E2-child")
    assert record is not None
    assert daemon._is_eligible(record) is False

    # Card is unchanged in backlog.
    after = repo.get_card("bTST-E2-child")
    assert after is not None
    assert after.status == CardStatus.BACKLOG.value
