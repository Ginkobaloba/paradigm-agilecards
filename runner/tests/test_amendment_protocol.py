"""Tests for the AC amendment protocol (chunk 4).

The executor signals the runner that AC needs revision by writing a
`change_request:` block into the card body and stamping the projected
card's `status:` field to `awaiting_amendment_review`. The daemon's
`_post_worker_exit` honors that signal:

- It does NOT run the verifier.
- It moves the card to `amendments` status.
- It clears `claimed_by` / `started_at` / `last_heartbeat` /
  `attempt_trace_id`.
- It preserves the worker's body (the change_request the human will
  review).
- It drops a marker at `signals/amendments/<card_id>.todo`.
- It NEVER edits the `acceptance_criteria:` block on its own
  initiative -- the runner only routes.
"""
from __future__ import annotations

import json
import textwrap
import uuid
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common.types import (
    EXIT_CLEAN,
    PROJECTED_CARD_NAME,
    WORKER_RESULT_NAME,
    ClaimedCard,
    DaemonConfig,
    RuntimePaths,
)
from cards_runner.daemon.daemon import Daemon, _WorkerHandle
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record, project_card_file
from cards_runner.store.sqlite_store import SqliteRepository


_AC_BLOCK = textwrap.dedent(
    """\
    ## Acceptance criteria

    ```yaml
    acceptance_criteria:
      - description: 'something_unreachable must exist'
        type: file_exists
        path: something_unreachable
    ```
    """
)


def _seed_card(repo: SqliteRepository, card_id: str) -> None:
    trace = str(uuid.uuid4())
    text = textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Amendment test
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

        """
    ) + _AC_BLOCK
    record = card_text_to_record(text, card_id_fallback=card_id)
    repo.create_card(record)


def _project_and_claim(
    repo: SqliteRepository, paths: RuntimePaths, card_id: str
) -> ClaimedCard:
    attempt = "att-" + card_id
    repo.claim_card(card_id, claimed_by="tester", attempt_trace_id=attempt)
    run_dir = paths.runs / attempt
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    card_file = run_dir / PROJECTED_CARD_NAME
    record = repo.get_card(card_id)
    assert record is not None
    project_card_file(record, card_file, verbatim=False)
    return ClaimedCard(
        card_id=card_id, attempt_trace_id=attempt, trace_id=attempt,
        run_dir=run_dir, worktree_path=worktree, card_file=card_file,
    )


def _stamp_worker_status_and_body(
    card_file: Path, *, status: str, change_request_body: str
) -> None:
    """Mutate the projected card file the way the worker would.

    Replace the frontmatter `status: active` (planted by the claim
    projection) with the supplied value, and replace the card body
    with a body that carries the executor's change_request block.
    """
    text = card_file.read_text(encoding="utf-8")
    text = text.replace("status: active\n", f"status: {status}\n", 1)
    # Append the change_request to the body.
    body_addition = textwrap.dedent(
        f"""\

        ## Change request

        ```yaml
        change_request:
          item_idx: 0
          reason: {change_request_body!r}
          proposed:
            description: 'something_reachable must exist'
            type: file_exists
            path: something_reachable
        ```
        """
    )
    card_file.write_text(text + body_addition, encoding="utf-8")


def _write_sidecar(run_dir: Path, payload: dict[str, Any]) -> None:
    (run_dir / WORKER_RESULT_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _handle(claim: ClaimedCard) -> _WorkerHandle:
    return _WorkerHandle(claim=claim, process=object(), spawned_at=0.0)  # type: ignore[arg-type]


# ---- tests --------------------------------------------------------


def test_amendment_status_routes_to_amendments(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    _seed_card(repo, "bTST-A0-amend")
    claim = _project_and_claim(repo, paths, "bTST-A0-amend")
    _stamp_worker_status_and_body(
        claim.card_file,
        status="awaiting_amendment_review",
        change_request_body="path is wrong; planner used a placeholder name",
    )
    _write_sidecar(claim.run_dir, {"exit_code": 0})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-A0-amend")
    assert card is not None
    assert card.status == CardStatus.AMENDMENTS.value
    # Claim provenance cleared.
    assert card.claimed_by is None
    assert card.started_at is None
    assert card.last_heartbeat is None
    assert card.attempt_trace_id is None
    # Body carries the change request the worker proposed.
    assert "change_request:" in card.body_md
    # Verifier did NOT run -- no `verified` event.
    types = [e.type for e in repo.list_events("bTST-A0-amend")]
    assert "verified" not in types
    assert "amended" in types


def test_amendment_marker_dropped_in_signals(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    _seed_card(repo, "bTST-A1-marker")
    claim = _project_and_claim(repo, paths, "bTST-A1-marker")
    _stamp_worker_status_and_body(
        claim.card_file, status="awaiting_amendment_review",
        change_request_body="AC item is impossible to satisfy",
    )
    _write_sidecar(claim.run_dir, {"exit_code": 0})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    marker = paths.signals / "amendments" / "bTST-A1-marker.todo"
    assert marker.is_file()
    contents = marker.read_text(encoding="utf-8")
    assert "bTST-A1-marker" in contents
    assert "attempt:" in contents


def test_runner_does_not_edit_acceptance_block(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    """The runner must never edit `acceptance_criteria:` on its own.

    Even if the executor proposes an amendment, the original AC stays
    intact for the human reviewer to compare against.
    """
    _seed_card(repo, "bTST-A2-immutable")
    claim = _project_and_claim(repo, paths, "bTST-A2-immutable")
    _stamp_worker_status_and_body(
        claim.card_file, status="awaiting_amendment_review",
        change_request_body="please relax the AC",
    )
    _write_sidecar(claim.run_dir, {"exit_code": 0})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-A2-immutable")
    assert card is not None
    # The original AC block is still present, unchanged.
    assert "something_unreachable" in card.body_md
    assert "acceptance_criteria:" in card.body_md


def test_short_form_amendments_status_also_routes(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    # A worker that writes the short-form status value still gets
    # routed correctly; both `awaiting_amendment_review` (the long
    # canonical name) and `amendments` (the subfolder short form) are
    # accepted as the amendment signal.
    _seed_card(repo, "bTST-A3-short")
    claim = _project_and_claim(repo, paths, "bTST-A3-short")
    _stamp_worker_status_and_body(
        claim.card_file, status="amendments",
        change_request_body="short status form",
    )
    _write_sidecar(claim.run_dir, {"exit_code": 0})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-A3-short")
    assert card is not None
    assert card.status == CardStatus.AMENDMENTS.value


def test_change_request_without_status_change_still_routes_with_warning(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An executor that wrote change_request but forgot to update status.

    The contract is strict that the status field is the trigger, but
    the runner is generous about the secondary signal: a body with
    `change_request:` and a status value that is not one of the known
    happy-path values is still routed to amendments. The runner logs
    a warning so the executor implementation gets fixed.
    """
    _seed_card(repo, "bTST-A4-sloppy")
    claim = _project_and_claim(repo, paths, "bTST-A4-sloppy")
    # Worker forgot to update the status field but did write a
    # change_request block. The projected card was stamped with
    # status=active by the projection (the runner's view of the claim);
    # the executor left it that way.
    _stamp_worker_status_and_body(
        claim.card_file, status="ambiguous",
        change_request_body="change request but status untouched",
    )
    _write_sidecar(claim.run_dir, {"exit_code": 0})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    with caplog.at_level("WARNING"):
        daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-A4-sloppy")
    assert card is not None
    assert card.status == CardStatus.AMENDMENTS.value
    assert any("change_request" in rec.message for rec in caplog.records)


def test_no_amendment_signal_runs_verifier(
    repo: SqliteRepository, paths: RuntimePaths,
    daemon_cfg_verifier: DaemonConfig,
) -> None:
    # A plain clean exit (no amendment status, no change_request) goes
    # to the verifier as before.
    _seed_card(repo, "bTST-A5-normal")
    claim = _project_and_claim(repo, paths, "bTST-A5-normal")
    # Satisfy the AC so the verifier passes.
    (claim.worktree_path / "something_unreachable").write_text("ok", encoding="utf-8")
    _write_sidecar(claim.run_dir, {"exit_code": 0})

    daemon = Daemon(daemon_cfg_verifier, repo=repo)
    daemon._post_worker_exit(_handle(claim), EXIT_CLEAN)

    card = repo.get_card("bTST-A5-normal")
    assert card is not None
    assert card.status == CardStatus.DONE.value
