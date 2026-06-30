"""v1 filesystem-to-store migration tests.

`C:\\dev\\todo` had no live cards at chunk 2a time, so the migration
is verified against a synthetic v1 corpus built here. The headline
assertion is losslessness, proven the way the design pass demands:
every imported card is projected straight back to Markdown and
byte-diffed against the file it came from.
"""
from __future__ import annotations

from pathlib import Path

from cards_runner.store.migrate_v1 import migrate_tree
from cards_runner.store.models import EventType
from cards_runner.store.projection import render_card_text
from cards_runner.store.repository import CardRepository
from .store_support import build_v1_tree

# A representative spread: cards in several subfolders, one carrying
# escalation history so the history-to-events path is exercised.
_LAYOUT = {
    "backlog": ["b001-01-alpha", "b001-02-beta"],
    "active": ["b001-03-gamma"],
    "done": ["b001-04-delta", "b002-01-escalated"],
    "blocked": ["b002-02-epsilon"],
}
_TOTAL_CARDS = sum(len(v) for v in _LAYOUT.values())


def _build_tree(tmp_path: Path) -> Path:
    root = tmp_path / "todo"
    assert build_v1_tree(root, _LAYOUT) == _TOTAL_CARDS
    return root


def test_migration_is_lossless(repo: CardRepository, tmp_path: Path) -> None:
    report = migrate_tree(_build_tree(tmp_path), repo)
    assert report.ok, report.summary()
    assert report.cards_seen == _TOTAL_CARDS
    assert report.cards_imported == _TOTAL_CARDS
    assert report.roundtrip_ok == _TOTAL_CARDS
    assert not report.failures


def test_migration_card_count_matches(repo: CardRepository, tmp_path: Path) -> None:
    migrate_tree(_build_tree(tmp_path), repo)
    assert repo.count_cards() == _TOTAL_CARDS


def test_migration_makes_subfolder_canonical(
    repo: CardRepository, tmp_path: Path
) -> None:
    migrate_tree(_build_tree(tmp_path), repo)
    # build_v1_tree stamps the frontmatter status to match the
    # subfolder; the importer trusts the subfolder regardless.
    gamma = repo.get_card("b001-03-gamma")
    epsilon = repo.get_card("b002-02-epsilon")
    assert gamma is not None and gamma.status == "active"
    assert epsilon is not None and epsilon.status == "blocked"


def test_migration_roundtrips_every_card_byte_for_byte(
    repo: CardRepository, tmp_path: Path
) -> None:
    root = _build_tree(tmp_path)
    migrate_tree(root, repo)
    for subfolder, card_ids in _LAYOUT.items():
        for card_id in card_ids:
            original = (root / subfolder / f"{card_id}.md").read_text(encoding="utf-8")
            stored = repo.get_card(card_id)
            assert stored is not None
            assert render_card_text(stored, verbatim=True) == original


def test_migration_emits_history_events(
    repo: CardRepository, tmp_path: Path
) -> None:
    migrate_tree(_build_tree(tmp_path), repo)
    # b002-01-escalated carries one cascade_history entry; it should
    # have become an `escalated` card_events row.
    events = repo.list_events("b002-01-escalated")
    escalated = [e for e in events if e.type == EventType.ESCALATED.value]
    assert len(escalated) == 1
    assert escalated[0].payload["source_field"] == "cascade_history"
    assert escalated[0].payload["from_tier"] == 2


def test_migration_writes_a_migrated_event_per_card(
    repo: CardRepository, tmp_path: Path
) -> None:
    migrate_tree(_build_tree(tmp_path), repo)
    for card_ids in _LAYOUT.values():
        for card_id in card_ids:
            types = [e.type for e in repo.list_events(card_id)]
            assert EventType.MIGRATED.value in types


def test_migration_rejects_a_nonempty_store(
    repo: CardRepository, tmp_path: Path
) -> None:
    root = _build_tree(tmp_path)
    assert migrate_tree(root, repo).ok
    second = migrate_tree(root, repo)
    assert not second.ok
    assert any("already has cards" in reason for _, reason in second.failures)


def test_migration_reports_a_malformed_card(
    repo: CardRepository, tmp_path: Path
) -> None:
    root = _build_tree(tmp_path)
    (root / "backlog" / "b001-99-broken.md").write_text(
        "this file has no frontmatter at all\n", encoding="utf-8"
    )
    report = migrate_tree(root, repo)
    assert not report.ok
    assert any("b001-99-broken" in path for path, _ in report.failures)
    # The well-formed cards still imported despite the one bad file.
    assert report.cards_imported == _TOTAL_CARDS


def test_migration_imports_batch_manifests(
    repo: CardRepository, tmp_path: Path
) -> None:
    root = _build_tree(tmp_path)
    batches = root / "_batches"
    batches.mkdir(exist_ok=True)
    (batches / "b001-manifest.yaml").write_text(
        "batch: b001\nsource:\n  text: the original story\n", encoding="utf-8"
    )
    report = migrate_tree(root, repo)
    assert report.ok, report.summary()
    assert report.batches_imported == 1
    stored = repo.get_batch("b001")
    assert stored is not None
    assert stored.manifest["source"]["text"] == "the original story"
    # The counter is seeded past the imported batch.
    assert repo.next_batch_id() == "b002"


def test_migration_of_missing_root_reports_failure(
    repo: CardRepository, tmp_path: Path
) -> None:
    report = migrate_tree(tmp_path / "no-such-todo", repo)
    assert not report.ok
    assert report.cards_seen == 0
