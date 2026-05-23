"""Chunk 6c: unblocker passes worktree= to gh.view_pr when project dir exists."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cards_runner.common.types import DaemonConfig
from cards_runner.daemon.pr_lifecycle import GhCallResult
from cards_runner.daemon.unblocker import unblock_merged_cards
from cards_runner.store import CardStatus
from cards_runner.store.projection import card_text_to_record
from cards_runner.store.sqlite_store import SqliteRepository

from tests.test_merge_gate import _card_text


@dataclass
class _RecordingGh:
    """Tiny gh fake that records view_pr's kwargs for assertion."""

    view_calls: list[dict[str, Any]] = field(default_factory=list)
    view_result: GhCallResult = field(
        default_factory=lambda: GhCallResult(
            ok=True, stdout='{"state":"OPEN","mergedAt":null}',
            parsed={"state": "OPEN", "mergedAt": None},
        )
    )

    def is_available(self) -> bool: return True
    def push(self, *a, **k): return GhCallResult(ok=True)
    def open_pr(self, *a, **k): return GhCallResult(ok=True)
    def merge_pr(self, *a, **k): return GhCallResult(ok=True)
    def pr_diff(self, *a, **k): return GhCallResult(ok=True)
    def pr_review(self, *a, **k): return GhCallResult(ok=True)

    def view_pr(
        self, *, identifier: str,
        fields: tuple[str, ...] = ("state", "mergedAt", "url"),
        worktree: Path | None = None,
    ) -> GhCallResult:
        self.view_calls.append({
            "identifier": identifier,
            "worktree": worktree,
        })
        return self.view_result


def _insert_blocked_card(
    repo: SqliteRepository, *, card_id: str, pr_url: str, project: str,
) -> None:
    record = card_text_to_record(_card_text(card_id, points=3))
    record.status = CardStatus.BLOCKED.value
    record.merge_status = "open"
    record.pr_url = pr_url
    record.project = project
    repo.create_card(record)


def test_unblocker_passes_project_dir_as_worktree_when_extant(
    repo: SqliteRepository, tmp_path: Path,
) -> None:
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    _insert_blocked_card(
        repo, card_id="bUWT-01",
        pr_url="https://github.com/x/y/pull/1",
        project=str(project_dir),
    )
    gh = _RecordingGh()
    unblock_merged_cards(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, pr_unblock_enabled=True),
    )
    assert len(gh.view_calls) == 1
    assert gh.view_calls[0]["worktree"] == project_dir


def test_unblocker_passes_none_worktree_when_project_missing(
    repo: SqliteRepository, tmp_path: Path,
) -> None:
    """A project path that doesn't exist on this host falls back to None."""
    _insert_blocked_card(
        repo, card_id="bUWT-02",
        pr_url="https://github.com/x/y/pull/2",
        project=str(tmp_path / "nope" / "does-not-exist"),
    )
    gh = _RecordingGh()
    unblock_merged_cards(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, pr_unblock_enabled=True),
    )
    assert gh.view_calls[0]["worktree"] is None


def test_unblocker_passes_none_when_project_field_empty(
    repo: SqliteRepository, tmp_path: Path,
) -> None:
    record = card_text_to_record(_card_text("bUWT-03", points=3))
    record.status = CardStatus.BLOCKED.value
    record.merge_status = "open"
    record.pr_url = "https://x/y/pull/3"
    record.project = None
    repo.create_card(record)
    gh = _RecordingGh()
    unblock_merged_cards(
        repo=repo, gh=gh,
        cfg=DaemonConfig(todo_root=tmp_path, pr_unblock_enabled=True),
    )
    assert gh.view_calls[0]["worktree"] is None
