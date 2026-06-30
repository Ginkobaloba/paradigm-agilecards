"""One-shot migration of v1 filesystem cards into a card store.

v1 keeps cards as Markdown files in status subfolders under a TODO
root (`C:\\dev\\todo` by default). This tool walks that tree, parses
every card, and writes it into a `CardRepository`. It then proves the
migration was lossless rather than asserting it: every imported card
is projected straight back to Markdown and byte-diffed against the
file it came from. The migration is lossless exactly when that diff
is empty across the whole corpus (storage_substrate_v2.md section
5.6).

Three things are written per card:

- The card row, with the verbatim frontmatter and body captured so
  the projection round-trip is exact.
- A `migrated` event, plus the `drafted` event `create_card` writes.
- One event per entry in the append-only history fields
  (`cascade_history` -> `escalated`, `verifier_cascade_history` ->
  `verified`). This is where the hand-rolled in-frontmatter event
  logs finally become real `card_events` rows.

Batch manifests under `_batches/` are imported into the `batches`
table when present, and the batch-id counter is seeded so the next
`/cards` run continues the sequence.

Scope note for chunk 2a: append-only blocks inside the card *body*
(completion notes, `change_request:` blocks) are preserved verbatim
in `body_md` but are not decomposed into events. Losslessness does
not depend on that decomposition; full body-block event extraction
is tracked as 2b/chunk-4 work.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ..common.types import ALL_SUBFOLDERS, now_utc_iso
from .models import DEFAULT_TENANT, ActorType, Batch, CardEvent, EventType
from .projection import ProjectionError, card_file_to_record, render_card_text
from .repository import CardRepository


@dataclass
class MigrationReport:
    """The outcome of a migration run.

    `ok` is the single question that matters: every card seen was
    imported, and every imported card round-trips byte-for-byte.
    """

    todo_root: str
    tenant_id: str
    subfolder_counts: dict[str, int] = field(default_factory=dict)
    cards_seen: int = 0
    cards_imported: int = 0
    events_emitted: int = 0
    batches_imported: int = 0
    roundtrip_checked: int = 0
    roundtrip_ok: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.failures
            and self.cards_seen == self.cards_imported
            and self.roundtrip_checked == self.cards_imported
            and self.roundtrip_ok == self.roundtrip_checked
        )

    def summary(self) -> str:
        lines = [
            f"migration report for {self.todo_root} (tenant {self.tenant_id})",
            f"  cards seen     : {self.cards_seen}",
            f"  cards imported : {self.cards_imported}",
            f"  events emitted : {self.events_emitted}",
            f"  batches        : {self.batches_imported}",
            f"  round-trip ok  : {self.roundtrip_ok}/{self.roundtrip_checked}",
            "  by subfolder   : "
            + ", ".join(f"{k}={v}" for k, v in sorted(self.subfolder_counts.items())),
        ]
        if self.failures:
            lines.append(f"  FAILURES ({len(self.failures)}):")
            lines.extend(f"    - {path}: {reason}" for path, reason in self.failures)
        lines.append(f"  RESULT: {'LOSSLESS' if self.ok else 'INCOMPLETE'}")
        return "\n".join(lines)


def migrate_tree(
    todo_root: str | Path,
    repo: CardRepository,
    *,
    tenant_id: str = DEFAULT_TENANT,
    allow_nonempty: bool = False,
) -> MigrationReport:
    """Migrate every v1 card under `todo_root` into `repo`.

    The repository must be empty unless `allow_nonempty` is set; the
    migration is one-shot by design and a half-populated store makes
    the count check meaningless.
    """
    root = Path(todo_root)
    report = MigrationReport(todo_root=str(root), tenant_id=tenant_id)
    if not root.is_dir():
        report.failures.append((str(root), "TODO root does not exist"))
        return report

    repo.initialize_schema()
    if not allow_nonempty and repo.count_cards(tenant_id=tenant_id) > 0:
        report.failures.append(
            (str(root), "store already has cards; pass allow_nonempty to override")
        )
        return report

    _migrate_batches(root, repo, tenant_id, report)

    for subfolder in ALL_SUBFOLDERS:
        sub_dir = root / subfolder
        if not sub_dir.is_dir():
            continue
        card_files = sorted(p for p in sub_dir.iterdir()
                            if p.is_file() and p.suffix == ".md")
        report.subfolder_counts[subfolder] = len(card_files)
        for card_file in card_files:
            report.cards_seen += 1
            _migrate_one_card(card_file, subfolder, repo, tenant_id, report)

    return report


def _migrate_one_card(
    card_file: Path,
    subfolder: str,
    repo: CardRepository,
    tenant_id: str,
    report: MigrationReport,
) -> None:
    """Import a single card file and verify its round trip."""
    original_text = card_file.read_text(encoding="utf-8")
    try:
        # The subfolder is canonical for status (RUNNER_CONTRACT.md).
        record = card_file_to_record(
            card_file, tenant_id=tenant_id, status_override=subfolder
        )
    except ProjectionError as exc:
        report.failures.append((str(card_file), f"parse failed: {exc}"))
        return

    try:
        repo.create_card(record)
    except Exception as exc:  # noqa: BLE001 - any store error is a failure.
        report.failures.append((str(card_file), f"import failed: {exc}"))
        return
    report.cards_imported += 1
    report.events_emitted += 1  # the drafted event create_card writes.

    report.events_emitted += _emit_history_events(record_card_id=record.card_id,
                                                   record_extra=record.frontmatter_extra,
                                                   tenant_id=tenant_id,
                                                   repo=repo)
    repo.append_event(
        CardEvent(
            card_id=record.card_id,
            tenant_id=tenant_id,
            type=EventType.MIGRATED.value,
            actor_type=ActorType.MIGRATION.value,
            at=now_utc_iso(),
            payload={"source_file": str(card_file), "subfolder": subfolder},
        )
    )
    report.events_emitted += 1

    # Prove losslessness: project the stored card straight back and
    # diff it against the file it came from.
    report.roundtrip_checked += 1
    stored = repo.get_card(record.card_id, tenant_id=tenant_id)
    if stored is None:
        report.failures.append((str(card_file), "card missing after import"))
        return
    projected = render_card_text(stored, verbatim=True)
    if projected == original_text:
        report.roundtrip_ok += 1
    else:
        report.failures.append(
            (str(card_file), "round-trip diff: projection != original bytes")
        )


def _emit_history_events(
    *,
    record_card_id: str,
    record_extra: dict[str, Any],
    tenant_id: str,
    repo: CardRepository,
) -> int:
    """Turn the in-frontmatter history lists into `card_events` rows.

    Returns the number of events emitted. `cascade_history` is the
    executor's tier-escalation log; `verifier_cascade_history` is the
    subjective evaluator's. Both are append-only by contract, which
    is precisely what an event row is.
    """
    emitted = 0
    for field_name, event_type, actor in (
        ("cascade_history", EventType.ESCALATED.value, ActorType.RUNNER.value),
        ("verifier_cascade_history", EventType.VERIFIED.value,
         ActorType.VERIFIER.value),
    ):
        entries = record_extra.get(field_name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            payload = dict(entry) if isinstance(entry, dict) else {"value": entry}
            at = payload.get("at") if isinstance(payload.get("at"), str) else None
            repo.append_event(
                CardEvent(
                    card_id=record_card_id,
                    tenant_id=tenant_id,
                    type=event_type,
                    actor_type=actor,
                    at=at or now_utc_iso(),
                    payload={"source_field": field_name, **payload},
                )
            )
            emitted += 1
    return emitted


def _migrate_batches(
    root: Path,
    repo: CardRepository,
    tenant_id: str,
    report: MigrationReport,
) -> None:
    """Import `_batches/*-manifest.yaml` and seed the batch counter."""
    batches_dir = root / "_batches"
    if not batches_dir.is_dir():
        return
    highest = 0
    for manifest_file in sorted(batches_dir.glob("*-manifest.yaml")):
        batch_id = manifest_file.name.split("-manifest.yaml")[0]
        try:
            manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            report.failures.append((str(manifest_file), f"manifest parse: {exc}"))
            continue
        if not isinstance(manifest, dict):
            manifest = {"raw": manifest}
        try:
            repo.create_batch(
                Batch(batch_id=batch_id, tenant_id=tenant_id, manifest=manifest)
            )
        except Exception as exc:  # noqa: BLE001
            report.failures.append((str(manifest_file), f"batch import: {exc}"))
            continue
        report.batches_imported += 1
        digits = "".join(ch for ch in batch_id if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    # Seed the counter so the next allocated id continues the run.
    for _ in range(highest):
        repo.next_batch_id(tenant_id=tenant_id)


def _build_repo(store_spec: str) -> CardRepository:
    """Build a repository from a `sqlite:PATH` or `dolt:DIR` spec."""
    from . import build_repository

    try:
        return build_repository(store_spec)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 only when the migration is lossless."""
    parser = argparse.ArgumentParser(
        prog="cards-runner-migrate",
        description="Migrate v1 filesystem cards into a card store.",
    )
    parser.add_argument(
        "--todo-root", required=True,
        help="v1 TODO root holding the status subfolders.",
    )
    parser.add_argument(
        "--store", required=True,
        help="target store: sqlite:PATH or dolt:DIR.",
    )
    parser.add_argument(
        "--tenant", default=DEFAULT_TENANT,
        help="tenant id to import under (default: %(default)s).",
    )
    parser.add_argument(
        "--allow-nonempty", action="store_true",
        help="permit importing into a store that already has cards.",
    )
    args = parser.parse_args(argv)

    repo = _build_repo(args.store)
    try:
        report = migrate_tree(
            args.todo_root,
            repo,
            tenant_id=args.tenant,
            allow_nonempty=args.allow_nonempty,
        )
    finally:
        repo.close()
    print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
