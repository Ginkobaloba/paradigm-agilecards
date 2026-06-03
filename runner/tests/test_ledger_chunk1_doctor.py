"""Doctor extensions for the ledger-chunk-1 schema.

Doctor now reports two things the chunk added:
1. The `cards.work_type` migration as a row in the existing
   `schema migrations` section (lands automatically via `ADDED_COLUMNS`).
2. A new `tables` section listing every `EXPECTED_TABLES` entry as
   present or MISSING. Catches a partial init the columns-only check
   would miss.
"""
from __future__ import annotations

from pathlib import Path

from cards_runner.cli.doctor import build_report, render_text
from cards_runner.common.types import DaemonConfig
from cards_runner.store.sqlite_store import SqliteRepository


def _cfg(tmp_path: Path) -> DaemonConfig:
    return DaemonConfig(
        todo_root=tmp_path,
        store_spec=f"sqlite:{tmp_path / 'cards.db'}",
    )


def test_schema_section_reports_work_type_applied_on_fresh_db(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    repo = SqliteRepository.open(cfg.resolved_store_spec()[len("sqlite:"):])
    try:
        report = build_report(cfg, repo=repo)
    finally:
        repo.close()
    work_type_row = next(
        (s for s in report.schema if s.table == "cards" and s.column == "work_type"),
        None,
    )
    assert work_type_row is not None, "doctor did not report cards.work_type"
    assert work_type_row.applied is True


def test_tables_section_reports_card_metrics_present(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    repo = SqliteRepository.open(cfg.resolved_store_spec()[len("sqlite:"):])
    try:
        report = build_report(cfg, repo=repo)
    finally:
        repo.close()
    tbl_names = {t.name: t.present for t in report.tables}
    assert tbl_names.get("card_metrics") is True
    assert tbl_names.get("metric_estimates") is True
    assert tbl_names.get("cards") is True


def test_tables_section_skipped_without_repo(tmp_path: Path) -> None:
    """When the operator calls doctor without --check-store the tables
    list is empty and the schema-skipped note explains why."""
    cfg = _cfg(tmp_path)
    report = build_report(cfg, repo=None)
    assert report.tables == []
    assert any("schema section skipped" in note for note in report.notes)


def test_render_text_includes_tables_section(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    repo = SqliteRepository.open(cfg.resolved_store_spec()[len("sqlite:"):])
    try:
        report = build_report(cfg, repo=repo)
    finally:
        repo.close()
    text = render_text(report)
    assert "tables:" in text
    assert "card_metrics: present" in text
    assert "metric_estimates: present" in text


def test_tables_section_flags_missing_tables(tmp_path: Path) -> None:
    """Hand-build a database with the cards table but neither of the
    ledger tables. Doctor should report card_metrics + metric_estimates
    as MISSING. Catches a half-initialized store."""
    import sqlite3
    db_path = tmp_path / "cards.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cards ("
        "tenant_id TEXT NOT NULL, card_id TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " frontmatter_extra TEXT NOT NULL DEFAULT '{}',"
        " frontmatter_raw TEXT NOT NULL DEFAULT '',"
        " body_md TEXT NOT NULL DEFAULT '',"
        " PRIMARY KEY (tenant_id, card_id))"
    )
    conn.commit()
    conn.close()

    # Build the report by opening the partial-init store WITHOUT calling
    # initialize_schema(). We construct the repo via the same connection
    # plumbing that doctor uses, so the introspection runs against the
    # actually-partial database.
    cfg = _cfg(tmp_path)

    class _PartialRepo:
        """Minimal repo shim exposing the `_conn` doctor reads."""

        def __init__(self, db: Path) -> None:
            self._conn = sqlite3.connect(str(db))

        def close(self) -> None:
            self._conn.close()

    repo = _PartialRepo(db_path)
    try:
        report = build_report(cfg, repo=repo)  # type: ignore[arg-type]
    finally:
        repo.close()
    tbl_names = {t.name: t.present for t in report.tables}
    assert tbl_names.get("cards") is True
    assert tbl_names.get("card_metrics") is False
    assert tbl_names.get("metric_estimates") is False
