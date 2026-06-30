/**
 * Sprint capacity meters. Three small horizontal bars (points / cost /
 * review hours), each colored by the metric's level (none/ok/warn/over).
 * A stoplight dot in the header summarizes the worst of the three so
 * the operator gets a one-glance answer to "is this sprint over its
 * binding constraint?".
 *
 * Capacity math lives in lib/sprintCapacity.ts; this file is render-only.
 */

import { formatCost } from "../lib/cost";
import {
  type CapacityLevel,
  type CapacityMetric,
  type SprintCapacity,
} from "../lib/sprintCapacity";

interface Props {
  capacity: SprintCapacity;
  /** Open the Edit sprint dialog so the operator can set targets. */
  onEditTargets: () => void;
}

const LEVEL_BAR: Record<CapacityLevel, string> = {
  none: "bg-border",
  ok: "bg-ok/60",
  warn: "bg-warn/70",
  over: "bg-danger/70",
};

const LEVEL_DOT: Record<CapacityLevel, string> = {
  none: "bg-muted/60",
  ok: "bg-ok",
  warn: "bg-warn",
  over: "bg-danger",
};

const LEVEL_LABEL: Record<CapacityLevel, string> = {
  none: "no targets",
  ok: "within budget",
  warn: "approaching budget",
  over: "over budget",
};

export function CapacityPanel({ capacity, onEditTargets }: Props) {
  const allUnset =
    capacity.points.target === null &&
    capacity.dollars.target === null &&
    capacity.reviewHours.target === null;

  return (
    <section className="surface flex flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <span
            className={`h-2.5 w-2.5 shrink-0 rounded-full ${LEVEL_DOT[capacity.overall]}`}
            aria-hidden
          />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-text">
            Capacity
          </h2>
          <span className="text-[10px] text-muted">
            {LEVEL_LABEL[capacity.overall]}
          </span>
        </div>
        <button
          type="button"
          onClick={onEditTargets}
          className="rounded border border-border bg-panel2 px-2 py-0.5 text-[10px] text-muted hover:text-text hover:border-accent/40"
        >
          {allUnset ? "Set targets" : "Edit targets"}
        </button>
      </div>
      <div className="flex flex-col gap-2 px-3 py-3">
        <MeterRow
          label="Points"
          metric={capacity.points}
          format={(n) => formatInt(n)}
        />
        <MeterRow
          label="Cost"
          metric={capacity.dollars}
          format={(n) => formatCost(n)}
          formatTarget={(n) => formatCost(n)}
        />
        <MeterRow
          label="Review"
          metric={capacity.reviewHours}
          format={(n) => formatHours(n)}
          formatTarget={(n) => formatHours(n)}
        />
        {allUnset ? (
          <p className="mt-1 text-[10px] text-muted italic">
            No targets set. Click "Set targets" to give the sprint a
            points / $ / review-hours budget.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function MeterRow({
  label,
  metric,
  format,
  formatTarget,
}: {
  label: string;
  metric: CapacityMetric;
  format: (n: number) => string;
  formatTarget?: (n: number) => string;
}) {
  const hasTarget = metric.target !== null;
  const fillPercent = hasTarget ? Math.min(100, metric.ratio * 100) : 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between text-[10px]">
        <span className="font-semibold uppercase tracking-wider text-muted">
          {label}
        </span>
        <span className="font-mono tabular-nums text-text">
          {format(metric.used)}
          {hasTarget ? (
            <>
              <span className="text-muted">/</span>
              {(formatTarget ?? format)(metric.target!)}
            </>
          ) : (
            <span className="ml-1 text-muted">(no target)</span>
          )}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel2">
        {hasTarget ? (
          <div
            className={`h-full ${LEVEL_BAR[metric.level]} transition-all`}
            style={{ width: `${fillPercent}%` }}
          />
        ) : (
          <div className="h-full w-0" />
        )}
      </div>
    </div>
  );
}

function formatInt(n: number): string {
  return Math.round(n).toString();
}

function formatHours(h: number): string {
  if (!Number.isFinite(h) || h <= 0) return "0h";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 10) return `${h.toFixed(1)}h`;
  return `${Math.round(h)}h`;
}
