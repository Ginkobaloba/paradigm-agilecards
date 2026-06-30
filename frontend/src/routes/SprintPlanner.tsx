/**
 * Sprint planner -- list view (v1).
 *
 * Lists all sprints (most recent first), with one-click create and a
 * toggle to surface archived sprints. Each row links to its detail
 * page (`/sprints/:id`), which is the planning surface for that
 * sprint -- assign cards, edit goal/dates/status, see budget meters.
 *
 * Capacity meters and drag-from-backlog assignment ship in follow-up
 * PRs; this surface gets the data flowing end-to-end so the rest can
 * iterate quickly.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  type SprintCreate,
  type SprintStatus,
  type SprintSummary,
  sprintsApi,
} from "../lib/api";
import { SprintFormDialog } from "../components/SprintFormDialog";

export function SprintPlanner() {
  const [sprints, setSprints] = useState<SprintSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(
    async (signal?: AbortSignal): Promise<void> => {
      try {
        const res = await sprintsApi.list({ includeArchived });
        if (signal?.aborted) return;
        setSprints(res.sprints);
        setError(null);
      } catch (err: unknown) {
        if (signal?.aborted) return;
        setError(err instanceof ApiError ? err.message : String(err));
      }
    },
    [includeArchived]
  );

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  const onCreate = async (body: SprintCreate): Promise<void> => {
    await sprintsApi.create(body);
    setCreateOpen(false);
    await load();
  };

  return (
    <div className="flex flex-col gap-4 px-5 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-sm font-semibold text-text">Sprints</h1>
          <span className="text-muted text-xs">
            {sprints
              ? `(${sprints.length})`
              : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-[11px] text-muted">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            show archived
          </label>
          <button
            type="button"
            className="rounded border border-accent/60 bg-accent/10 px-2 py-1 text-[11px] font-semibold text-accent hover:bg-accent/20"
            onClick={() => setCreateOpen(true)}
          >
            + New sprint
          </button>
        </div>
      </div>

      {error ? (
        <div className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}

      {sprints === null ? (
        <SkeletonList />
      ) : sprints.length === 0 ? (
        <EmptyState onCreate={() => setCreateOpen(true)} />
      ) : (
        <ul className="flex flex-col gap-2">
          {sprints.map((s) => (
            <li key={s.id}>
              <SprintRow sprint={s} />
            </li>
          ))}
        </ul>
      )}

      <SprintFormDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={onCreate}
        mode="create"
      />
    </div>
  );
}

function SprintRow({ sprint }: { sprint: SprintSummary }) {
  return (
    <Link
      to={`/sprints/${sprint.id}`}
      className="surface flex items-center gap-4 px-4 py-3 hover:border-accent/40 transition-colors"
    >
      <StatusBadge status={sprint.status} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-text truncate">
            {sprint.name}
          </span>
          {sprint.archivedAt ? (
            <span className="rounded border border-muted/40 px-1.5 py-0 text-[10px] uppercase tracking-wider text-muted">
              archived
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 text-[11px] text-muted truncate">
          {formatDateRange(sprint.startsAt, sprint.endsAt)}
          {sprint.goal ? <span className="mx-2">·</span> : null}
          {sprint.goal}
        </div>
      </div>
      <div className="flex items-center gap-3 text-[11px] text-muted shrink-0">
        <Stat label="cards" value={String(sprint.cardCount)} />
        <Stat
          label="pts"
          value={
            sprint.plannedPointsSum > 0
              ? sprint.pointsTarget !== null
                ? `${sprint.plannedPointsSum}/${sprint.pointsTarget}`
                : String(sprint.plannedPointsSum)
              : sprint.pointsTarget !== null
                ? `0/${sprint.pointsTarget}`
                : "—"
          }
        />
      </div>
    </Link>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-baseline gap-1 font-mono tabular-nums">
      <span className="text-text">{value}</span>
      <span className="uppercase tracking-wider text-[9px] opacity-70">
        {label}
      </span>
    </span>
  );
}

function StatusBadge({ status }: { status: SprintStatus }) {
  const tone = STATUS_TONE[status];
  return (
    <span
      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tone}`}
    >
      {status}
    </span>
  );
}

const STATUS_TONE: Record<SprintStatus, string> = {
  planning: "border-muted/50 bg-panel2 text-muted",
  active: "border-accent/60 bg-accent/[0.08] text-accent",
  completed: "border-ok/40 bg-ok/[0.08] text-ok",
  cancelled: "border-danger/40 bg-danger/[0.08] text-danger",
};

function SkeletonList() {
  return (
    <ul className="flex flex-col gap-2">
      {[0, 1, 2].map((i) => (
        <li
          key={i}
          className="surface flex animate-pulse items-center gap-4 px-4 py-3"
        >
          <div className="h-5 w-16 rounded-full bg-border" />
          <div className="flex-1">
            <div className="h-3 w-1/3 rounded bg-border" />
            <div className="mt-1 h-2 w-1/2 rounded bg-border/60" />
          </div>
        </li>
      ))}
    </ul>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="surface flex flex-col items-center gap-3 px-6 py-12 text-center">
      <p className="text-sm font-semibold text-text">No sprints yet</p>
      <p className="max-w-md text-xs text-muted leading-relaxed">
        A sprint is a time-boxed batch of work. Create one, assign cards from
        the backlog, and the dashboard will track points + cost against your
        target so you know when you're at capacity.
      </p>
      <button
        type="button"
        className="rounded border border-accent/60 bg-accent/10 px-3 py-1 text-[11px] font-semibold text-accent hover:bg-accent/20"
        onClick={onCreate}
      >
        Create the first sprint
      </button>
    </div>
  );
}

function formatDateRange(start: string, end: string): string {
  // Both come in as ISO date or ISO datetime. Show just the date part.
  const s = start.slice(0, 10);
  const e = end.slice(0, 10);
  return `${s} → ${e}`;
}
