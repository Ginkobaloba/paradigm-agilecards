"""Import legacy board data (markdown cards + board.sqlite) into Postgres.

One-shot cutover tool for retiring ``legacy/board-express/``. Reads the
legacy filesystem card tree (status folders of ``.md`` files with YAML
frontmatter) and the legacy SQLite database (ranks, card events, saved views,
sprints, sprint_cards, retros), and writes everything into the new Postgres
schema under a single org.

Safety properties:

- Connects as the RLS-bound app role (``--database-url``) with the target org
  bound, so the import physically cannot write outside ``--org``.
- Refuses to run if the target org already has cards (no silent merge);
  there is deliberately no --force.
- ``--dry-run`` prints the full plan and writes nothing.
- The whole import is ONE transaction: any failure rolls back everything.

This script is run manually, once, at cutover time -- with Drew's explicit
go-ahead (Tier-3 rule). It does not delete or modify any legacy data.

Usage (from backend/, venv active, deps: pip install -e .[tools]):

    python scripts/import_legacy.py \
        --cards-dir C:\\dev\\todo \
        --sqlite ..\\legacy\\board-express\\backend\\data\\board.sqlite \
        --org org_paradigm \
        --views-owner user_drew \
        --database-url postgresql+psycopg://agilecards_app:...@host:5432/agilecards \
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cards_api.db import Database  # noqa: E402
from cards_api.models import (  # noqa: E402
    Card,
    CardEvent,
    CardRank,
    Retro,
    SavedView,
    Sprint,
    SprintCard,
)

# Legacy folder -> status id (legacy fs/cards.ts detectStatusFromPath).
FOLDER_TO_STATUS = {
    "backlog": "backlog",
    "active": "active",
    "amendments": "awaiting_amendment_review",
    "done": "done",
    "blocked": "blocked",
}


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split a legacy card file into (frontmatter dict, body). BOM-tolerant;
    a file without a frontmatter fence is all body (legacy behavior)."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "pyyaml is required: pip install -e .[tools] (from backend/)"
        ) from exc

    content = raw.lstrip("﻿")
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    loaded = yaml.safe_load(parts[1]) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    return loaded, parts[2].lstrip("\n")


def load_cards(cards_dir: Path) -> list[dict]:
    cards: list[dict] = []
    for folder, status in FOLDER_TO_STATUS.items():
        folder_path = cards_dir / folder
        if not folder_path.is_dir():
            continue
        for md in sorted(folder_path.glob("*.md")):
            frontmatter, body = parse_frontmatter(md.read_text(encoding="utf-8"))
            card_id = str(frontmatter.get("id") or md.stem)
            cards.append(
                {
                    "id": card_id,
                    "status": status,
                    "frontmatter": frontmatter,
                    "body": body,
                    "mtime": datetime.fromtimestamp(md.stat().st_mtime, tz=UTC),
                }
            )
    return cards


def load_sqlite(db_path: Path) -> dict[str, list[dict]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    out: dict[str, list[dict]] = {}
    tables = {
        "card_rank": "SELECT card_id, status, rank FROM card_rank",
        "card_events": "SELECT card_id, type, at, details FROM card_events ORDER BY id",
        "saved_views": (
            "SELECT name, payload, created_at, updated_at FROM saved_views ORDER BY id"
        ),
        "sprints": (
            "SELECT id, name, starts_at, ends_at, goal, status, points_target,"
            " dollar_target, review_hours_target, archived_at FROM sprints ORDER BY id"
        ),
        "sprint_cards": "SELECT sprint_id, card_id, planned_points FROM sprint_cards",
        "retros": "SELECT sprint_id, held_on, summary FROM retros ORDER BY id",
    }
    for name, query in tables.items():
        try:
            out[name] = [dict(r) for r in conn.execute(query).fetchall()]
        except sqlite3.OperationalError:
            out[name] = []  # table absent in older DBs -- import what exists
    conn.close()
    return out


def run(args: argparse.Namespace) -> int:
    cards_dir = Path(args.cards_dir)
    sqlite_path = Path(args.sqlite)
    if not cards_dir.is_dir():
        raise SystemExit(f"cards dir not found: {cards_dir}")
    if not sqlite_path.is_file():
        raise SystemExit(f"sqlite db not found: {sqlite_path}")

    cards = load_cards(cards_dir)
    legacy = load_sqlite(sqlite_path)
    card_ids = {c["id"] for c in cards}

    print(f"plan: org={args.org}")
    print(f"  cards:        {len(cards)}")
    print(f"  ranks:        {len(legacy['card_rank'])}")
    print(f"  card_events:  {len(legacy['card_events'])}")
    print(f"  saved_views:  {len(legacy['saved_views'])} (owner_sub={args.views_owner})")
    print(f"  sprints:      {len(legacy['sprints'])}")
    print(f"  sprint_cards: {len(legacy['sprint_cards'])}")
    print(f"  retros:       {len(legacy['retros'])}")
    orphan_ranks = [r for r in legacy["card_rank"] if r["card_id"] not in card_ids]
    if orphan_ranks:
        print(f"  note: {len(orphan_ranks)} rank rows reference absent cards -- skipped")
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    db = Database(args.database_url)
    org = args.org
    with db.org_session(org) as s:
        existing = s.execute(text("SELECT count(*) FROM cards")).scalar_one()
        if existing:
            raise SystemExit(
                f"org {org!r} already has {existing} cards; refusing to merge an import"
            )

        for c in cards:
            s.add(
                Card(
                    org_id=org,
                    id=c["id"],
                    status=c["status"],
                    frontmatter=c["frontmatter"],
                    body=c["body"],
                    updated_at=c["mtime"],
                )
            )
        s.flush()

        for r in legacy["card_rank"]:
            if r["card_id"] not in card_ids:
                continue
            s.add(
                CardRank(
                    org_id=org,
                    card_id=r["card_id"],
                    status=r["status"],
                    rank=float(r["rank"]),
                )
            )

        for e in legacy["card_events"]:
            details = e["details"]
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (TypeError, ValueError):
                    details = {"raw": details}
            # SQLite stores `at` as an ISO-ish TEXT ("YYYY-MM-DD HH:MM:SS");
            # the Postgres column is timestamptz (legacy timestamps are UTC).
            at = datetime.fromisoformat(e["at"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            s.add(
                CardEvent(
                    org_id=org,
                    card_id=e["card_id"],
                    type=e["type"],
                    at=at,
                    details=details,
                )
            )

        for v in legacy["saved_views"]:
            payload = v["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    payload = None
            s.add(
                SavedView(
                    org_id=org,
                    owner_sub=args.views_owner,
                    name=v["name"],
                    payload=payload,
                )
            )

        sprint_id_map: dict[int, int] = {}
        for sp in legacy["sprints"]:
            sprint = Sprint(
                org_id=org,
                name=sp["name"],
                starts_at=sp["starts_at"],
                ends_at=sp["ends_at"],
                goal=sp["goal"],
                status=sp["status"] or "planning",
                points_target=sp["points_target"],
                dollar_target=sp["dollar_target"],
                review_hours_target=sp["review_hours_target"],
                archived_at=sp["archived_at"],
            )
            s.add(sprint)
            s.flush()
            sprint_id_map[sp["id"]] = sprint.id

        for link in legacy["sprint_cards"]:
            new_sprint_id = sprint_id_map.get(link["sprint_id"])
            if new_sprint_id is None:
                continue
            s.add(
                SprintCard(
                    org_id=org,
                    sprint_id=new_sprint_id,
                    card_id=link["card_id"],
                    planned_points=link["planned_points"],
                )
            )

        for r in legacy["retros"]:
            s.add(
                Retro(
                    org_id=org,
                    sprint_id=sprint_id_map.get(r["sprint_id"]),
                    held_on=r["held_on"],
                    summary=r["summary"],
                )
            )

    print("import committed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cards-dir", required=True, help="legacy cards tree (CARDS_DIR)")
    parser.add_argument("--sqlite", required=True, help="legacy board.sqlite path")
    parser.add_argument("--org", required=True, help="target org_id for every imported row")
    parser.add_argument(
        "--views-owner",
        default="legacy-import",
        help="owner_sub for imported saved views (legacy token scoping has no JWT sub)",
    )
    parser.add_argument("--database-url", required=True, help="app-role Postgres DSN")
    parser.add_argument("--dry-run", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
