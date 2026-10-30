"""Re-score saved replies offline and diff them against the recorded rows.

The bridge for any scorer change (Daedalus 2026-10-30): before and after a
harness refactor, every saved reply is scored again by the CURRENT scorer,
with no model and no GPU, and each row's scoring fields are compared with
what the arm recorded. Identical on every row is the evidence that a new
scorer hash scores exactly as the old one did.

A reply file is only re-scorable if it is the reply the arm scored: its
sha256 must equal the row's `reply_sha256`. Anything else is reported as
not re-scorable, never as a scoring difference.

    python rescore.py --results results/A-... [--results results/B-...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import run_bakeoff as rb

# What the scorer decides. Timing, tokens and model fields come from the
# model and cannot change with the scorer, so they are not compared.
COMPARED = ("passed", "parsed", "parse_failure", "blocks", "touched_tests",
            "suspect", "forged", "verifier_timeout", "think_in_block",
            "raw_passed", "outcome")


@dataclass
class Report:
    rows: int = 0
    identical: int = 0
    skipped_infra: int = 0
    unscorable: list[str] = field(default_factory=list)
    differences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unscorable and not self.differences and self.identical == self.rows


def _read_reply(path: Path) -> str | None:
    # Bytes, decoded without newline translation: `read_text` would turn the
    # CRLF that `write_text` produced back into LF only by accident.
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def rescore_row(row: dict[str, Any], card: rb.Card, reply_text: str,
                score: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """What the current scorer records for this row's reply."""
    stripped, flags = rb.strip_think(reply_text)
    record: dict[str, Any] = dict(flags)
    record.update(score(card, stripped))
    record["raw_passed"] = (score(card, reply_text)["passed"]
                            if stripped != reply_text else record["passed"])
    reply = rb.Reply(reply_text, row.get("started_at") or 0.0, row.get("ended_at") or 0.0,
                     prompt_tokens=row.get("prompt_tokens"),
                     output_tokens=row.get("output_tokens"),
                     done_reason=row.get("done_reason"), error_kind=row.get("error_kind"))
    record["outcome"] = ("infra_error" if record.get("harness_error")
                         else rb.outcome(record, reply, rb.NUM_CTX))
    return record


def rescore_dir(results: Path, *, score: Callable[..., dict[str, Any]] | None = None,
                cards: list[rb.Card] | None = None) -> Report:
    score = score or rb.score_reply
    by_id = {c.card_id: c for c in (cards or rb.load_cards())}
    report = Report()
    for line in (results / "runs.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("error_kind") == "infra":
            report.skipped_infra += 1
            continue
        report.rows += 1
        key = f"{results.name}/{row['card_id']}-r{row['run']}"
        reply = _read_reply(results / "replies" / f"{row['card_id']}-r{row['run']}.txt")
        if reply is None:
            report.unscorable.append(f"{key}: reply file missing")
            continue
        if hashlib.sha256(reply.encode()).hexdigest() != row.get("reply_sha256"):
            report.unscorable.append(f"{key}: saved reply does not match reply_sha256")
            continue
        now = rescore_row(row, by_id[row["card_id"]], reply, score)
        diff = {k: {"recorded": row.get(k), "now": now.get(k)}
                for k in COMPARED if row.get(k) != now.get(k)}
        if diff:
            report.differences.append({"row": key, "diff": diff})
        else:
            report.identical += 1
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, action="append", required=True)
    args = ap.parse_args(argv)
    total = Report()
    for results in args.results:
        r = rescore_dir(results)
        print(f"{results.name}: {r.identical}/{r.rows} identical, "
              f"{len(r.differences)} differ, {len(r.unscorable)} not re-scorable, "
              f"{r.skipped_infra} infra rows skipped")
        total.rows += r.rows
        total.identical += r.identical
        total.skipped_infra += r.skipped_infra
        total.unscorable += r.unscorable
        total.differences += r.differences
    for u in total.unscorable:
        print(f"NOT RE-SCORABLE {u}")
    for d in total.differences:
        print(f"DIFFERS {d['row']}: {json.dumps(d['diff'], sort_keys=True)}")
    hashes = {name: rb._sha256(rb.HERE / name) for name in rb.SCORER_FILES}
    print(f"TOTAL {total.identical}/{total.rows} identical; scorer now {json.dumps(hashes)}")
    return 0 if total.ok else 1


if __name__ == "__main__":
    sys.exit(main())
