"""Tests for `cards-runner stats recalibrate`."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from cards_runner.cli.__main__ import main as cli_main


def _seed(db: Path, **fields) -> None:
    conn = sqlite3.connect(str(db))
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    conn.execute(
        f"INSERT INTO card_metrics ({cols}) VALUES ({placeholders})",
        tuple(fields.values()),
    )
    conn.commit()
    conn.close()


def test_stats_recalibrate_empty_prints_message(
    tmp_path: Path, capsys
) -> None:
    db = tmp_path / "cards.db"
    rc = cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no populated buckets" in out


def test_stats_recalibrate_populates_estimates(
    tmp_path: Path, capsys
) -> None:
    db = tmp_path / "cards.db"
    # initialize_schema by opening once through the CLI; recalibrate
    # is the explicit cycle but the store has to exist first.
    cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
    ])
    capsys.readouterr()

    _seed(
        db,
        tenant_id="default", card_id="bF-1", work_type="feature", tier=3,
        agent_wall_seconds=600.0, executor_tokens_total=30000,
        human_review_wall_seconds=240.0, rework_cycles=0,
        contract_survived=1, incomplete_metrics=0,
        regression_card_ids="[]", updated_at="2026-05-29T00:00:00Z",
    )
    rc = cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "feature/tier3" in out
    assert "n=1" in out

    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        "SELECT n_samples FROM metric_estimates "
        "WHERE work_type = 'feature' AND tier = 3"
    )
    rows = cur.fetchall()
    conn.close()
    assert rows == [(1,)]


def test_stats_recalibrate_json_output(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cards.db"
    cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
    ])
    capsys.readouterr()
    _seed(
        db,
        tenant_id="default", card_id="bR-1", work_type="refactor", tier=2,
        agent_wall_seconds=300.0, regression_card_ids="[]",
        incomplete_metrics=0, updated_at="2026-05-29T00:00:00Z",
    )
    rc = cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
        "--json",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["tenant"] == "default"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["work_type"] == "refactor"
    assert payload["results"][0]["tier"] == 2
    assert payload["results"][0]["n_samples"] == 1


def test_stats_recalibrate_respects_tenant_flag(
    tmp_path: Path, capsys
) -> None:
    db = tmp_path / "cards.db"
    cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
    ])
    capsys.readouterr()
    _seed(
        db,
        tenant_id="other", card_id="bO-1", work_type="feature", tier=3,
        agent_wall_seconds=600.0, regression_card_ids="[]",
        incomplete_metrics=0, updated_at="2026-05-29T00:00:00Z",
    )
    # Default tenant: empty.
    rc = cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
    ])
    assert rc == 0
    assert "no populated buckets" in capsys.readouterr().out
    # `--tenant other`: one bucket.
    rc = cli_main([
        "stats", "recalibrate",
        "--todo-root", str(tmp_path),
        "--store", f"sqlite:{db}",
        "--tenant", "other",
    ])
    assert rc == 0
    assert "feature/tier3" in capsys.readouterr().out
