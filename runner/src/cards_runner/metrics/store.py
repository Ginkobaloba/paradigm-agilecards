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

from dataclasses import dataclass
from typing import Any, Protocol


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


__all__ = [
    "CardMetricsRow",
    "MetricEstimateRow",
    "MetricsStore",
]
