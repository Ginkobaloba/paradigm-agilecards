"""Ledger-table SQL surface for the metrics package.

The cards table is owned by `CardRepository`; the ledger tables
(`card_metrics`, `metric_estimates`) share the same SQLite / Dolt
connection but are owned by this module. Keeping the ledger surface
out of `CardRepository` is deliberate -- the ledger is a read-side
concern that should not appear on the daemon's hot-path interface.

The connection abstraction is intentionally narrow: anything exposing
`execute(sql, params) -> cursor` works. The wrapper extracts the
connection from a `CardRepository` (mirroring doctor.py's pattern) so
callers can hand the existing repository over without learning new
plumbing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from ..common.types import now_utc_iso


class _Connection(Protocol):
    """Subset of the DB-API connection interface this module needs."""

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any: ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class CardMetricsRow:
    """One `card_metrics` row, the input to the estimator.

    Only the fields the estimator reads are surfaced. The full schema
    (24 columns) carries more, but recalibration only needs the
    samples that drive the rollup math.
    """

    card_id: str
    work_type: str | None
    tier: int | None
    agent_wall_seconds: float | None
    executor_tokens_total: int | None
    human_review_wall_seconds: float | None
    rework_cycles: int | None
    contract_survived: int | None
    incomplete_metrics: int


@dataclass(frozen=True)
class CardMetricsFullRow:
    """The complete `card_metrics` row the chunk-2 writer persists.

    Mirrors `schema.CARD_METRICS_COLUMNS` field-for-field (minus the
    derived `updated_at`, which the upsert stamps). This is the write
    side; `CardMetricsRow` above is the narrow read projection the
    estimator consumes. Keeping them separate stops the estimator's hot
    read from carrying twenty-odd columns it never looks at.

    `regression_card_ids` is the decoded list; the store serializes it
    to the JSON TEXT column. Booleans (`pin_required`, `contract_survived`,
    `incomplete_metrics`) are stored as 0/1 integers to match the schema
    (SQLite has no native bool; Dolt uses TINYINT).
    """

    tenant_id: str
    card_id: str
    work_type: str | None
    tier: int | None
    pin_required: bool | None
    contract_authored_at: str | None
    started_at: str | None
    finished_at: str | None
    agent_wall_seconds: float | None
    agent_attempts: int | None
    executor_tokens_total: int | None
    executor_cost_usd: float | None
    verifier_tokens_total: int | None
    reviewer_tokens_total: int | None
    human_review_wall_seconds: float | None
    rework_cycles: int | None
    diff_lines_added: int | None
    diff_lines_removed: int | None
    merge_gate: str | None
    merged_at: str | None
    regression_card_ids: tuple[str, ...]
    contract_survived: bool | None
    incomplete_metrics: bool


@dataclass(frozen=True)
class MetricEstimateRow:
    """One `metric_estimates` row, the output the orchestrator persists.

    Mirrors the schema column shape (`int` for `executor_tokens_p*`
    since the column is INTEGER; floats elsewhere) so the upsert sql
    is literal."""

    tenant_id: str
    work_type: str
    tier: int
    n_samples: int
    agent_wall_seconds_p50: float | None
    agent_wall_seconds_p75: float | None
    agent_wall_seconds_p90: float | None
    executor_tokens_p50: int | None
    executor_tokens_p90: int | None
    human_review_wall_seconds_p50: float | None
    human_review_wall_seconds_p90: float | None
    rework_rate_mean: float
    contract_survival_rate: float
    last_calibrated_at: str
    prior_weight: float


class MetricsStore:
    """Read-write surface for the ledger tables.

    Construct via `MetricsStore.from_repository(repo)` to share the
    repository's connection. Tests can pass a raw connection in.
    """

    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    @classmethod
    def from_repository(cls, repo: object) -> "MetricsStore":
        """Extract the SQL connection from a `CardRepository`.

        Mirrors `cli.doctor`'s introspection: tries `_conn` first, then
        the legacy `conn` attribute. A repo without a connection-like
        attribute raises -- the metrics store cannot operate without
        SQL access, and silently degrading would surprise the caller."""
        conn = getattr(repo, "_conn", None) or getattr(repo, "conn", None)
        if conn is None:
            raise TypeError(
                "metrics store requires a CardRepository with a SQL "
                "connection attribute (`_conn` or `conn`)"
            )
        return cls(conn)

    # ---- reads --------------------------------------------------------

    def list_buckets(self, *, tenant_id: str) -> list[tuple[str, int]]:
        """Return the distinct `(work_type, tier)` pairs present in
        `card_metrics`, skipping rows whose work_type or tier is NULL
        (those rows cannot be bucketed; the estimator excludes them).
        Pairs come back deterministically ordered so test output is
        stable across SQLite / Dolt."""
        cur = self._conn.execute(
            "SELECT DISTINCT work_type, tier FROM card_metrics "
            "WHERE tenant_id = ? AND work_type IS NOT NULL "
            "AND tier IS NOT NULL "
            "ORDER BY work_type, tier",
            (tenant_id,),
        )
        return [(str(row[0]), int(row[1])) for row in cur.fetchall()]

    def fetch_bucket_samples(
        self,
        *,
        tenant_id: str,
        work_type: str,
        tier: int,
    ) -> list[CardMetricsRow]:
        """Return the `card_metrics` rows for one bucket, in
        insertion-stable order. The estimator filters out
        `incomplete_metrics=1` rows internally so they do not poison
        the percentile output."""
        cur = self._conn.execute(
            "SELECT card_id, work_type, tier, agent_wall_seconds,"
            " executor_tokens_total, human_review_wall_seconds,"
            " rework_cycles, contract_survived, incomplete_metrics"
            " FROM card_metrics"
            " WHERE tenant_id = ? AND work_type = ? AND tier = ?"
            " ORDER BY card_id",
            (tenant_id, work_type, tier),
        )
        return [_row_to_card_metrics(row) for row in cur.fetchall()]

    def get_card_metrics(
        self, *, tenant_id: str, card_id: str
    ) -> CardMetricsFullRow | None:
        """Return the full `card_metrics` row for one card, or None.

        Used by the writer's rebuild path and by the audit-log replay
        verification (spec section 12.3) to compare a live row against
        one rebuilt from the event log.
        """
        cur = self._conn.execute(
            "SELECT tenant_id, card_id, work_type, tier, pin_required,"
            " contract_authored_at, started_at, finished_at,"
            " agent_wall_seconds, agent_attempts, executor_tokens_total,"
            " executor_cost_usd, verifier_tokens_total,"
            " reviewer_tokens_total, human_review_wall_seconds,"
            " rework_cycles, diff_lines_added, diff_lines_removed,"
            " merge_gate, merged_at, regression_card_ids,"
            " contract_survived, incomplete_metrics"
            " FROM card_metrics"
            " WHERE tenant_id = ? AND card_id = ?",
            (tenant_id, card_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_card_metrics_full(row)

    def bucket_regression(
        self, *, tenant_id: str, work_type: str, tier: int
    ) -> tuple[int, int]:
        """Return (n_cards, n_regressed) for a `(work_type, tier)` bucket.

        A card "regressed" if its `regression_card_ids` is a non-empty
        JSON list (a follow-up bugfix cited it). The confidence gate's
        historical-floor (spec 3.5) reads the rate; the trust-signal read
        (a later chunk) refines the definition with a rolling window."""
        cur = self._conn.execute(
            "SELECT regression_card_ids FROM card_metrics"
            " WHERE tenant_id = ? AND work_type = ? AND tier = ?",
            (tenant_id, work_type, tier),
        )
        rows = cur.fetchall()
        n = len(rows)
        regressed = sum(
            1 for r in rows if _decode_id_list(r[0])
        )
        return n, regressed

    def get_estimate(
        self,
        *,
        tenant_id: str,
        work_type: str,
        tier: int,
    ) -> MetricEstimateRow | None:
        cur = self._conn.execute(
            "SELECT tenant_id, work_type, tier, n_samples,"
            " agent_wall_seconds_p50, agent_wall_seconds_p75,"
            " agent_wall_seconds_p90, executor_tokens_p50,"
            " executor_tokens_p90, human_review_wall_seconds_p50,"
            " human_review_wall_seconds_p90, rework_rate_mean,"
            " contract_survival_rate, last_calibrated_at, prior_weight"
            " FROM metric_estimates"
            " WHERE tenant_id = ? AND work_type = ? AND tier = ?",
            (tenant_id, work_type, tier),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_estimate(row)

    def list_estimates(self, *, tenant_id: str) -> list[MetricEstimateRow]:
        cur = self._conn.execute(
            "SELECT tenant_id, work_type, tier, n_samples,"
            " agent_wall_seconds_p50, agent_wall_seconds_p75,"
            " agent_wall_seconds_p90, executor_tokens_p50,"
            " executor_tokens_p90, human_review_wall_seconds_p50,"
            " human_review_wall_seconds_p90, rework_rate_mean,"
            " contract_survival_rate, last_calibrated_at, prior_weight"
            " FROM metric_estimates"
            " WHERE tenant_id = ?"
            " ORDER BY work_type, tier",
            (tenant_id,),
        )
        return [_row_to_estimate(row) for row in cur.fetchall()]

    # ---- writes -------------------------------------------------------

    def upsert_card_metrics(self, row: CardMetricsFullRow) -> None:
        """Replace the card's `card_metrics` row. Idempotent.

        Keyed on `(tenant_id, card_id)` via INSERT OR REPLACE, exactly
        as spec section 5.4 prescribes. The writer always supplies the
        full row (rebuilt by folding the card's event log), so REPLACE
        never loses a column. `updated_at` is stamped here so callers do
        not have to.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO card_metrics"
            " (tenant_id, card_id, work_type, tier, pin_required,"
            "  contract_authored_at, started_at, finished_at,"
            "  agent_wall_seconds, agent_attempts, executor_tokens_total,"
            "  executor_cost_usd, verifier_tokens_total,"
            "  reviewer_tokens_total, human_review_wall_seconds,"
            "  rework_cycles, diff_lines_added, diff_lines_removed,"
            "  merge_gate, merged_at, regression_card_ids,"
            "  contract_survived, incomplete_metrics, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?, ?)",
            (
                row.tenant_id, row.card_id, row.work_type, row.tier,
                _bool_to_int(row.pin_required), row.contract_authored_at,
                row.started_at, row.finished_at, row.agent_wall_seconds,
                row.agent_attempts, row.executor_tokens_total,
                row.executor_cost_usd, row.verifier_tokens_total,
                row.reviewer_tokens_total, row.human_review_wall_seconds,
                row.rework_cycles, row.diff_lines_added,
                row.diff_lines_removed, row.merge_gate, row.merged_at,
                json.dumps(list(row.regression_card_ids)),
                _bool_to_int(row.contract_survived),
                int(row.incomplete_metrics), now_utc_iso(),
            ),
        )

    def upsert_estimate(self, row: MetricEstimateRow) -> None:
        """Replace the bucket's `metric_estimates` row. Idempotent.

        The recalibration loop runs this on every refresh; the spec's
        section 5.4 idempotency rule says cumulative fields are derived
        from `card_metrics`, not from the previous estimate row, so
        REPLACE (rather than incremental UPDATE) is the correct verb.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO metric_estimates"
            " (tenant_id, work_type, tier, n_samples,"
            "  agent_wall_seconds_p50, agent_wall_seconds_p75,"
            "  agent_wall_seconds_p90, executor_tokens_p50,"
            "  executor_tokens_p90, human_review_wall_seconds_p50,"
            "  human_review_wall_seconds_p90, rework_rate_mean,"
            "  contract_survival_rate, last_calibrated_at, prior_weight)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.tenant_id, row.work_type, row.tier, row.n_samples,
                row.agent_wall_seconds_p50, row.agent_wall_seconds_p75,
                row.agent_wall_seconds_p90,
                row.executor_tokens_p50, row.executor_tokens_p90,
                row.human_review_wall_seconds_p50,
                row.human_review_wall_seconds_p90,
                row.rework_rate_mean, row.contract_survival_rate,
                row.last_calibrated_at, row.prior_weight,
            ),
        )

    def commit(self) -> None:
        self._conn.commit()


def _row_to_card_metrics(row: Any) -> CardMetricsRow:
    return CardMetricsRow(
        card_id=str(row[0]),
        work_type=_opt_str(row[1]),
        tier=_opt_int(row[2]),
        agent_wall_seconds=_opt_float(row[3]),
        executor_tokens_total=_opt_int(row[4]),
        human_review_wall_seconds=_opt_float(row[5]),
        rework_cycles=_opt_int(row[6]),
        contract_survived=_opt_int(row[7]),
        incomplete_metrics=int(row[8] or 0),
    )


def _row_to_card_metrics_full(row: Any) -> CardMetricsFullRow:
    return CardMetricsFullRow(
        tenant_id=str(row[0]),
        card_id=str(row[1]),
        work_type=_opt_str(row[2]),
        tier=_opt_int(row[3]),
        pin_required=_opt_bool(row[4]),
        contract_authored_at=_opt_str(row[5]),
        started_at=_opt_str(row[6]),
        finished_at=_opt_str(row[7]),
        agent_wall_seconds=_opt_float(row[8]),
        agent_attempts=_opt_int(row[9]),
        executor_tokens_total=_opt_int(row[10]),
        executor_cost_usd=_opt_float(row[11]),
        verifier_tokens_total=_opt_int(row[12]),
        reviewer_tokens_total=_opt_int(row[13]),
        human_review_wall_seconds=_opt_float(row[14]),
        rework_cycles=_opt_int(row[15]),
        diff_lines_added=_opt_int(row[16]),
        diff_lines_removed=_opt_int(row[17]),
        merge_gate=_opt_str(row[18]),
        merged_at=_opt_str(row[19]),
        regression_card_ids=_decode_id_list(row[20]),
        contract_survived=_opt_bool(row[21]),
        incomplete_metrics=bool(row[22] or 0),
    )


def _row_to_estimate(row: Any) -> MetricEstimateRow:
    return MetricEstimateRow(
        tenant_id=str(row[0]),
        work_type=str(row[1]),
        tier=int(row[2]),
        n_samples=int(row[3] or 0),
        agent_wall_seconds_p50=_opt_float(row[4]),
        agent_wall_seconds_p75=_opt_float(row[5]),
        agent_wall_seconds_p90=_opt_float(row[6]),
        executor_tokens_p50=_opt_int(row[7]),
        executor_tokens_p90=_opt_int(row[8]),
        human_review_wall_seconds_p50=_opt_float(row[9]),
        human_review_wall_seconds_p90=_opt_float(row[10]),
        rework_rate_mean=float(row[11] or 0.0),
        contract_survival_rate=float(row[12] or 0.0),
        last_calibrated_at=str(row[13] or ""),
        prior_weight=float(row[14] or 0.0),
    )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _bool_to_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _decode_id_list(value: Any) -> tuple[str, ...]:
    """Decode the `regression_card_ids` JSON TEXT column to a tuple.

    The column is `NOT NULL DEFAULT '[]'`, so a well-formed row always
    holds a JSON array. A malformed value degrades to empty rather than
    raising: the audit log is authoritative, and a corrupt denormalized
    cell should not crash a read."""
    if value is None:
        return ()
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(item) for item in decoded)


__all__ = [
    "CardMetricsFullRow",
    "CardMetricsRow",
    "MetricEstimateRow",
    "MetricsStore",
]
